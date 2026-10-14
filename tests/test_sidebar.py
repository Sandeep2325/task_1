import time

from django.test import override_settings

from integrations import registry
from workspace.models import PlacedItem

from .base import PROJECT, IntegrationTestCase

URL = f"/api/projects/{PROJECT}/items/DRV-03/sidebar"
SIZING_DOWN = {"latency_s": (0.0, 0.0), "failure_rate": 1.0}


class SidebarTests(IntegrationTestCase):
    async def test_all_sections_ok_when_systems_are_healthy(self):
        body = await self.get_json(URL)
        sections = body["sections"]
        self.assertEqual(list(sections), ["sizing", "catalogue", "pricing"])
        self.assertEqual({s["status"] for s in sections.values()}, {"ok"})
        self.assertEqual(sections["sizing"]["data"]["variant_code"], "CWD-R7-090-4")
        self.assertEqual(sections["catalogue"]["data"]["frame"], "R7")
        self.assertEqual(sections["pricing"]["data"]["currency"], "EUR")

    async def test_sizing_down_gives_partial_result_not_an_error(self):
        with override_settings(MOCK_SIZING=SIZING_DOWN):
            body = await self.get_json(URL)
        sections = body["sections"]
        self.assertEqual(sections["sizing"]["status"], "unavailable")
        self.assertIn("503", sections["sizing"]["error"])
        self.assertEqual(sections["catalogue"]["status"], "ok")
        self.assertEqual(sections["pricing"]["status"], "ok")

    async def test_sizing_down_falls_back_to_persisted_result_as_stale(self):
        # Persist a result through a re-size, then drop the cache and break the tool.
        await self.post_json(
            f"/api/projects/{PROJECT}/items/DRV-03/resize", {"power_kw": 75, "voltage": 400, "load_pct": 100}
        )
        from django.core.cache import cache

        await cache.aclear()
        with override_settings(MOCK_SIZING=SIZING_DOWN):
            body = await self.get_json(URL)
        sizing = body["sections"]["sizing"]
        self.assertEqual(sizing["status"], "stale")
        self.assertEqual(sizing["data"]["variant_code"], "CWD-R7-090-4")
        self.assertIsNotNone(sizing["fetched_at"])

    async def test_cached_section_served_stale_when_upstream_fails_after_ttl(self):
        await self.get_json(URL)
        conf = {**self._integrations(), "sizing": {**self._integrations()["sizing"], "ttl_s": 0}}
        with override_settings(INTEGRATIONS=conf, MOCK_SIZING=SIZING_DOWN):
            body = await self.get_json(URL)
        self.assertEqual(body["sections"]["sizing"]["status"], "stale")

    async def test_slow_sizing_answers_within_budget_and_finishes_in_background(self):
        with override_settings(SIDEBAR_BUDGET_S=0.3, MOCK_SIZING={"latency_s": (0.8, 0.8), "failure_rate": 0.0}):
            started = time.monotonic()
            body = await self.get_json(URL)
            elapsed = time.monotonic() - started
            sizing = body["sections"]["sizing"]
            self.assertLess(elapsed, 0.7)
            self.assertEqual(sizing["status"], "unavailable")
            self.assertTrue(sizing["pending"])
            self.assertEqual(body["sections"]["catalogue"]["status"], "ok")

            await registry.drain()  # the run completes after the response and warms the cache
            body = await self.get_json(URL)
        self.assertEqual(body["sections"]["sizing"]["status"], "ok")
        self.assertEqual(self.calls["sizing"], 1)

    async def test_sections_are_cached_per_system(self):
        await self.get_json(URL)
        await self.get_json(URL)
        self.assertEqual(self.calls, {"sizing": 1, "catalogue": 1, "pricing": 1})

    async def test_cabinet_has_no_sizing_panel(self):
        body = await self.get_json(f"/api/projects/{PROJECT}/items/CAB-01/sidebar")
        self.assertEqual(list(body["sections"]), ["catalogue", "pricing"])

    async def test_unknown_item_is_404(self):
        await self.get_json(f"/api/projects/{PROJECT}/items/NOPE/sidebar", expect=404)

    async def test_sse_stream_delivers_slow_sizing_result(self):
        with override_settings(MOCK_SIZING={"latency_s": (0.2, 0.2), "failure_rate": 0.0}):
            response = await self.async_client.get(
                f"/api/projects/{PROJECT}/items/DRV-03/sizing/stream", headers=self.headers()
            )
            self.assertEqual(response["Content-Type"], "text/event-stream")
            chunks = [c async for c in response.streaming_content]
        payload = b"".join(chunks).decode()
        self.assertIn("event: sizing", payload)
        self.assertIn('"status": "ok"', payload)

    @staticmethod
    def _integrations():
        from django.conf import settings

        return settings.INTEGRATIONS


class ResilienceThroughAdapterTests(IntegrationTestCase):
    async def test_retry_with_backoff_recovers_from_a_transient_failure(self):
        from unittest import mock

        from mocks import systems

        class Scripted:  # first sizing attempt fails, the retry succeeds
            outcomes = iter([0.0, 0.99])

            def uniform(self, a, b):
                return a

            def random(self):
                return next(self.outcomes)

        with mock.patch.object(systems, "_rng", Scripted()):
            with override_settings(MOCK_SIZING={"latency_s": (0.0, 0.0), "failure_rate": 0.5}):
                body = await self.get_json(URL)
        self.assertEqual(body["sections"]["sizing"]["status"], "ok")
        self.assertEqual(self.calls["sizing"], 2)

    async def test_circuit_breaker_stops_calling_a_failing_sizing_tool(self):
        sizing = registry.get("sizing")
        with override_settings(MOCK_SIZING=SIZING_DOWN):
            for load in range(50, 56):  # distinct inputs so the cache never answers
                await PlacedItem.objects.filter(project_id=PROJECT, tag="DRV-03").aupdate(
                    sizing_inputs={"power_kw": 75, "voltage": 400, "load_pct": load}
                )
                await self.get_json(URL)
            self.assertEqual(sizing.breaker.state, "open")
            calls_when_open = self.calls["sizing"]
            body = await self.get_json(URL.replace("DRV-03", "DRV-04"))
        self.assertEqual(self.calls["sizing"], calls_when_open)  # no upstream call while open
        self.assertIn("circuit open", body["sections"]["sizing"]["error"])

    async def test_pricing_rate_limit_queues_then_refuses_past_the_deadline(self):
        # Pricing allows 5/s (one every 200 ms). Eight concurrent calls with a
        # 500 ms deadline: three fit (t=0, 0.2, 0.4), five are refused locally
        # without ever reaching the ERP, so callers can fall back immediately.
        import asyncio

        from integrations.context import Context, ItemSnapshot, Principal
        from integrations.resilience import RateLimited
        from mocks.systems import CATALOGUE

        pricing = registry.get("pricing")
        principal = Principal("u", "sales_engineer", "DE")
        deadline = time.monotonic() + 0.5
        outcomes = await asyncio.gather(
            *(pricing.call(Context(principal, ItemSnapshot.for_variant(c)), deadline) for c in list(CATALOGUE)[:8]),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(o, dict) for o in outcomes), 3)
        self.assertEqual(sum(isinstance(o, RateLimited) for o in outcomes), 5)
        self.assertEqual(self.calls["pricing"], 3)  # and the ERP never answered 429
