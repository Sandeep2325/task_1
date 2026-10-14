from collections import Counter

import httpx
from django.core.cache import cache
from django.test import TestCase, override_settings

from integrations import http, registry
from mocks import systems
from workspace import auth, demo

PROJECT = demo.PROJECT_ID

# Deterministic mocks by default; individual tests make them slow or broken.
FAST_MOCKS = {
    "MOCK_SIZING": {"latency_s": (0.0, 0.0), "failure_rate": 0.0},
    "RETRY_BACKOFF": {"base_s": 0.01, "max_s": 0.02},
}


@override_settings(**FAST_MOCKS)
class IntegrationTestCase(TestCase):
    """Adapters talk to the real mock systems through httpx.MockTransport (no network)."""

    def setUp(self):
        cache.clear()
        registry.reset_state()
        systems.reset()
        self.calls = Counter()

        async def handler(request: httpx.Request):
            parts = request.url.path.strip("/").split("/")
            self.calls[parts[1] if parts[0] == "mock" else parts[0]] += 1
            return await systems.handle(request)

        http.use_transport(httpx.MockTransport(handler))
        self.addCleanup(http.use_transport, None)
        demo.seed()

    @staticmethod
    def headers(role="sales_engineer", country="DE", sub=None, **claims):
        token = auth.mint_dev_token(sub=sub or f"{role}-test", role=role, country=country, **claims)
        return {"Authorization": f"Bearer {token}"}

    async def get_json(self, url, expect=200, **kw):
        response = await self.async_client.get(url, headers=kw.pop("headers", None) or self.headers(**kw))
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    async def post_json(self, url, body, expect=200, **kw):
        response = await self.async_client.post(
            url, body, content_type="application/json", headers=kw.pop("headers", None) or self.headers(**kw)
        )
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()
