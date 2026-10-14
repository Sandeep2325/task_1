import json
from pathlib import Path

import httpx
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from integrations import registry
from workspace import auth, demo

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


# --- Dev console and mock switches (dev only) ------------------------------------

CONSOLE = Path(__file__).parent / "console.html"


@require_GET
def console(request):
    return HttpResponse(CONSOLE.read_bytes(), content_type="text/html; charset=utf-8")


def control(request):
    """GET the mock switches, or POST {sizing_failure_rate, sizing_latency_s: [min, max]} to change them."""
    if request.method == "POST":
        try:
            body = json.loads(request.body or b"{}")
            if "sizing_failure_rate" in body:
                systems.overrides["sizing_failure_rate"] = min(1.0, max(0.0, float(body["sizing_failure_rate"])))
            if "sizing_latency_s" in body:
                lo, hi = (float(v) for v in body["sizing_latency_s"])
                systems.overrides["sizing_latency_s"] = (max(0.0, lo), max(lo, hi))
        except (ValueError, TypeError):
            return JsonResponse({"error": "invalid_json"}, status=400)
    conf = systems.sizing_conf()
    return JsonResponse({"sizing_failure_rate": conf["failure_rate"], "sizing_latency_s": list(conf["latency_s"])})


@require_POST
def reset_demo(request):
    """Re-seed P-24117 and clear caches, breakers and mock switches."""
    cache.clear()
    registry.reset_state()
    systems.reset()
    demo.seed()
    return JsonResponse({"status": "reset"})
