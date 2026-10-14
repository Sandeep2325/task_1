from integrations import registry
from integrations.base import Adapter
from integrations.context import Context

from .base import PROJECT, IntegrationTestCase


class HarmonicsAdapter(Adapter):
    """What a fourth system looks like: one class, no change to the others."""

    name = "harmonics"
    panel_order = 40

    @property
    def conf(self):
        return {"ttl_s": 60, "stale_ttl_s": 600, "call_timeout_s": 1.0, "retries": 0}

    def applies_to(self, ctx: Context) -> bool:
        return ctx.item.kind == "drive"

    def cache_key(self, ctx: Context) -> str:
        return ctx.item.variant_code

    async def fetch(self, ctx: Context, timeout: float) -> dict:
        return {"thd_pct": 4.2, "variant_code": ctx.item.variant_code}


class RegistryTests(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        registry.register(HarmonicsAdapter)
        self.addCleanup(registry.unregister, "harmonics")

    async def test_new_adapter_appears_in_sidebar(self):
        body = await self.get_json(f"/api/projects/{PROJECT}/items/DRV-03/sidebar")
        self.assertEqual(list(body["sections"]), ["sizing", "catalogue", "pricing", "harmonics"])
        self.assertEqual(body["sections"]["harmonics"]["data"]["thd_pct"], 4.2)

    async def test_new_adapter_respects_applies_to(self):
        body = await self.get_json(f"/api/projects/{PROJECT}/items/CAB-01/sidebar")
        self.assertNotIn("harmonics", body["sections"])
