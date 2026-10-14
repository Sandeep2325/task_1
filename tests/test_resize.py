from django.test import override_settings

from workspace.models import PlacedItem

from .base import PROJECT, IntegrationTestCase

URL = f"/api/projects/{PROJECT}/items/DRV-03/resize"


class ResizeTests(IntegrationTestCase):
    async def item(self, tag="DRV-03"):
        return await PlacedItem.objects.aget(project_id=PROJECT, tag=tag)

    async def test_higher_load_swaps_variant_model_and_bom(self):
        before = await self.item()
        body = await self.post_json(URL, {"power_kw": 75, "voltage": 400, "load_pct": 130})

        self.assertTrue(body["variant_changed"])
        self.assertEqual(body["previous_variant"], "CWD-R7-090-4")
        self.assertEqual(body["item"]["variant_code"], "CWD-R8-132-4")
        self.assertEqual(body["item"]["model_ref"]["asset_id"], "cwd-r8")
        self.assertEqual(body["item"]["id"], str(before.id))  # identity survives the re-size
        self.assertEqual(body["item"]["revision"], before.revision + 1)
        self.assertEqual(body["bom_change"]["lines"], [
            {"variant_code": "CWD-R7-090-4", "qty_before": 1, "qty_after": 0, "delta": -1},
            {"variant_code": "CWD-R8-132-4", "qty_before": 0, "qty_after": 1, "delta": 1},
        ])
        self.assertEqual(body["bom_change"]["price_change"]["status"], "ok")
        self.assertGreater(body["bom_change"]["price_change"]["delta"], 0)
        # R8 does not fit the 800 mm cabinet: flagged, not blocked.
        self.assertEqual(len(body["warnings"]), 1)
        self.assertIn("does not fit cabinet CAB-01", body["warnings"][0])

        after = await self.item()
        self.assertEqual(after.variant_code, "CWD-R8-132-4")
        self.assertEqual(after.sizing_inputs["load_pct"], 130)
        bom = await self.get_json(f"/api/projects/{PROJECT}/bom")
        self.assertIn("CWD-R8-132-4", [line["variant_code"] for line in bom["lines"]])
        self.assertNotIn("CWD-R7-090-4", [line["variant_code"] for line in bom["lines"]])

    async def test_sidebar_after_resize_is_consistent_without_another_sizing_call(self):
        await self.post_json(URL, {"power_kw": 75, "voltage": 400, "load_pct": 130})
        calls = self.calls["sizing"]
        body = await self.get_json(f"/api/projects/{PROJECT}/items/DRV-03/sidebar")
        sections = body["sections"]
        self.assertEqual(sections["sizing"]["data"]["variant_code"], "CWD-R8-132-4")
        self.assertEqual(sections["catalogue"]["data"]["variant_code"], "CWD-R8-132-4")
        self.assertEqual(sections["pricing"]["data"]["variant_code"], "CWD-R8-132-4")
        self.assertEqual(self.calls["sizing"], calls)

    async def test_same_variant_updates_inputs_only(self):
        body = await self.post_json(URL, {"power_kw": 75, "voltage": 400, "load_pct": 110})
        self.assertFalse(body["variant_changed"])
        self.assertIsNone(body["bom_change"])
        self.assertEqual(body["item"]["variant_code"], "CWD-R7-090-4")
        self.assertEqual(body["warnings"], [])

    async def test_channel_partner_resize_has_no_price_in_bom_change(self):
        body = await self.post_json(URL, {"power_kw": 75, "voltage": 400, "load_pct": 130}, role="channel_partner")
        self.assertEqual(body["bom_change"]["price_change"], {"status": "not_permitted"})

    async def test_sizing_down_leaves_item_unchanged(self):
        before = await self.item()
        with override_settings(MOCK_SIZING={"latency_s": (0.0, 0.0), "failure_rate": 1.0}):
            body = await self.post_json(URL, {"power_kw": 75, "voltage": 400, "load_pct": 130}, expect=503)
        self.assertEqual(body["error"], "upstream_unavailable")
        after = await self.item()
        self.assertEqual((after.variant_code, after.revision), (before.variant_code, before.revision))

    async def test_stale_revision_is_rejected(self):
        body = await self.post_json(
            URL, {"power_kw": 75, "voltage": 400, "load_pct": 130, "expected_revision": 99}, expect=409
        )
        self.assertEqual(body["error"], "conflict")

    async def test_no_variant_fits(self):
        body = await self.post_json(URL, {"power_kw": 400, "voltage": 400, "load_pct": 100}, expect=422)
        self.assertIn("no_variant_fits", body["detail"])

    async def test_invalid_inputs(self):
        await self.post_json(URL, {"power_kw": -1, "voltage": 400, "load_pct": 100}, expect=400)
        await self.post_json(URL, {"power_kw": 75, "voltage": 230, "load_pct": 100}, expect=400)
        await self.post_json(URL, {"voltage": 400}, expect=400)

    async def test_only_drives_can_be_resized(self):
        await self.post_json(
            f"/api/projects/{PROJECT}/items/CAB-01/resize", {"power_kw": 75, "voltage": 400, "load_pct": 100}, expect=400
        )
