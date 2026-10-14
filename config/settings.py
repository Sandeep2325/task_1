"""Django settings for the Cloudworx sidebar integration service.

Everything that tunes an upstream system (TTL, rate limit, retries, breaker)
lives in INTEGRATIONS so the per-system policy is visible in one place.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-secret-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver,0.0.0.0").split(",")

INSTALLED_APPS = [
    "integrations.apps.IntegrationsConfig",
    "workspace.apps.WorkspaceConfig",
    "assistant.apps.AssistantConfig",
    "mocks.apps.MocksConfig",
]

# Bearer-token API: no sessions, no cookies, so no CSRF middleware.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

# Local memory by default so the service runs with zero infrastructure.
# Set REDIS_URL to share the cache across replicas (see README / design doc).
if env("REDIS_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env("REDIS_URL")}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "integrations"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"httpx": {"level": "WARNING"}},
}

# --- Identity -----------------------------------------------------------------
# In production OIDC_JWKS_URL points at the customer's IdP. Without it the
# service trusts the local dev key that the mock issuer signs with.
OIDC_ISSUER = env("OIDC_ISSUER", "http://127.0.0.1:8000/mock/oidc")
OIDC_AUDIENCE = env("OIDC_AUDIENCE", "cloudworx-workspace-api")
OIDC_JWKS_URL = env("OIDC_JWKS_URL")
DEV_KEYS_DIR = Path(env("DEV_KEYS_DIR", str(BASE_DIR / ".dev-keys")))

# --- Upstream systems -----------------------------------------------------------
MOCKS_ENABLED = env_bool("MOCKS_ENABLED", True)
MOCK_BASE_URL = env("MOCK_BASE_URL", "http://127.0.0.1:8000/mock").rstrip("/")

INTEGRATIONS = {
    "catalogue": {
        "base_url": f"{MOCK_BASE_URL}/catalogue",
        "ttl_s": 15 * 60,  # changes arrive via nightly export + change feed
        "stale_ttl_s": 24 * 3600,
        "call_timeout_s": 3.0,
        "retries": 2,
    },
    "sizing": {
        "base_url": f"{MOCK_BASE_URL}/sizing",
        "ttl_s": 6 * 3600,  # deterministic for the same inputs and tool version
        "stale_ttl_s": 7 * 24 * 3600,
        "call_timeout_s": 10.0,  # a run takes 1-6 s; keeps running after the sidebar budget
        "retries": 2,
        "rate_per_s": 20,
        "burst": 1,
        "breaker": {"failure_threshold": 5, "reset_after_s": 30},
    },
    "pricing": {
        "base_url": f"{MOCK_BASE_URL}/pricing",
        "ttl_s": 5 * 60,
        "stale_ttl_s": 24 * 3600,  # never beyond the quote's own valid_until
        "call_timeout_s": 3.0,
        "retries": 2,
        "rate_per_s": 5,
        "burst": 1,  # never more than 5 calls in any 1 s window
    },
}
RETRY_BACKOFF = {"base_s": 0.1, "max_s": 1.0}

SIDEBAR_BUDGET_S = float(env("SIDEBAR_BUDGET_S", "0.9"))  # 1 s p95 target minus headroom
BOM_BUDGET_S = float(env("BOM_BUDGET_S", "3.0"))
RESIZE_TIMEOUT_S = float(env("RESIZE_TIMEOUT_S", "10.0"))
CONFIRMATION_TTL_S = 5 * 60

# Behaviour of the mock enterprise systems (see mocks/systems.py).
MOCK_SIZING = {
    "latency_s": (float(env("MOCK_SIZING_MIN_S", "0.5")), float(env("MOCK_SIZING_MAX_S", "3.0"))),
    "failure_rate": float(env("MOCK_SIZING_FAILURE_RATE", "0.2")),
}
MOCK_PRICING = {"rate_per_s": int(env("MOCK_PRICING_RATE_PER_S", "5"))}
