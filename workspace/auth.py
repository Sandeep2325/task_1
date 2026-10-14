"""Bearer-token verification.

Checks signature, issuer, audience and expiry, then maps claims to a Principal.
In production the key comes from the IdP's JWKS (OIDC_JWKS_URL). In dev it is
a local RSA key that the mock issuer in mocks/ signs with.
"""

import functools
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings

from integrations.context import ADMINISTRATOR, CHANNEL_PARTNER, SALES_ENGINEER, Principal

ROLES = frozenset({SALES_ENGINEER, CHANNEL_PARTNER, ADMINISTRATOR})
DEV_KID = "dev-key-1"


class AuthError(Exception):
    pass


def principal_from_request(request) -> Principal:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("missing bearer token")
    return verify(token)


def verify(token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            _signing_key(token),
            algorithms=["RS256"],
            audience=settings.OIDC_AUDIENCE,
            issuer=settings.OIDC_ISSUER,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=30,
        )
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e
    role = claims.get("role")
    if role not in ROLES:
        raise AuthError("unknown or missing role claim")
    if not claims.get("country"):
        raise AuthError("missing country claim")
    return Principal(
        sub=claims["sub"],
        role=role,
        country=claims["country"],
        customer_group=claims.get("customer_group", "standard"),
        token=token,
    )


def _signing_key(token: str):
    if settings.OIDC_JWKS_URL:
        return _jwks_client(settings.OIDC_JWKS_URL).get_signing_key_from_jwt(token).key
    return _dev_private_key().public_key()


@functools.cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True)


# --- dev key and mock issuer support ------------------------------------------


@functools.cache
def _dev_private_key() -> rsa.RSAPrivateKey:
    path = settings.DEV_KEYS_DIR / "oidc-dev-private.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return key


def dev_jwks() -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_dev_private_key().public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": DEV_KID, "use": "sig", "alg": "RS256"}]}


def mint_dev_token(*, sub, role, country, customer_group="standard", ttl_s=3600, key=None, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.OIDC_ISSUER,
        "aud": settings.OIDC_AUDIENCE,
        "sub": sub,
        "role": role,
        "country": country,
        "customer_group": customer_group,
        "iat": now,
        "exp": now + ttl_s,
        "jti": str(uuid.uuid4()),
        **overrides,
    }
    return jwt.encode(claims, key or _dev_private_key(), algorithm="RS256", headers={"kid": DEV_KID})
