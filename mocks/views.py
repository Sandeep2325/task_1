import json

import httpx
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from workspace import auth

from . import systems


async def upstream(request, path):
    """Expose the mock enterprise systems over HTTP."""
    proxied = httpx.Request(
        request.method,
        request.build_absolute_uri(),
        headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
        content=request.body,
    )
    response = await systems.handle(proxied)
    out = HttpResponse(response.content, status=response.status_code, content_type="application/json")
    if "retry-after" in response.headers:
        out["Retry-After"] = response.headers["retry-after"]
    return out


# --- Mock OpenID Connect issuer (dev only) --------------------------------------


@require_GET
def oidc_configuration(request):
    return JsonResponse({
        "issuer": settings.OIDC_ISSUER,
        "jwks_uri": f"{settings.OIDC_ISSUER}/jwks",
        "token_endpoint": f"{settings.OIDC_ISSUER}/token",
        "id_token_signing_alg_values_supported": ["RS256"],
    })


@require_GET
def oidc_jwks(request):
    return JsonResponse(auth.dev_jwks())


@require_POST
def oidc_token(request):
    """Mint a token for a test user. Stands in for the IdP login + token exchange."""
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid_json"}, status=400)
    role = body.get("role", "sales_engineer")
    if role not in auth.ROLES:
        return JsonResponse({"error": f"role must be one of {sorted(auth.ROLES)}"}, status=400)
    token = auth.mint_dev_token(
        sub=body.get("sub", f"{role}-demo"),
        role=role,
        country=body.get("country", "DE"),
        customer_group=body.get("customer_group", "standard"),
    )
    return JsonResponse({"access_token": token, "token_type": "Bearer", "expires_in": 3600})
