from assistant.models import AuditEvent
from workspace.models import PlacedItem

from .base import PROJECT, IntegrationTestCase

TOOLS = "/api/assistant/tools"


class AssistantToolTests(IntegrationTestCase):
    async def call(self, name, arguments, expect=200, **kw):
        return await self.post_json(f"{TOOLS}/{name}", {"arguments": arguments}, expect=expect, **kw)

    async def test_tool_definitions_are_listed(self):
        body = await self.get_json(TOOLS)
        self.assertEqual({t["name"] for t in body["tools"]}, {"get_item_details", "get_bom_cost", "resize_item"})

    async def test_bom_cost_honours_entitlements(self):
        engineer = await self.call("get_bom_cost", {"project_id": PROJECT}, role="sales_engineer")
        partner = await self.call("get_bom_cost", {"project_id": PROJECT}, role="channel_partner")
        self.assertIn("EUR", engineer["result"]["pricing"]["totals"])
        self.assertEqual(partner["result"]["pricing"], {"status": "not_permitted"})
        self.assertEqual(partner["_meta"]["trust"], "untrusted_data")

    async def test_item_details_hide_price_for_partner(self):
        body = await self.call("get_item_details", {"project_id": PROJECT, "item_tag": "DRV-03"}, role="channel_partner")
        self.assertEqual(body["result"]["sections"]["pricing"]["status"], "not_permitted")

    async def test_resize_tool_does_not_write_without_confirmation(self):
        args = {"project_id": PROJECT, "item_tag": "DRV-03", "power_kw": 75, "voltage": 400, "load_pct": 130}
        body = await self.call("resize_item", args, sub="alice")
        self.assertEqual(body["result"]["status"], "confirmation_required")
        item = await PlacedItem.objects.aget(project_id=PROJECT, tag="DRV-03")
        self.assertEqual(item.variant_code, "CWD-R7-090-4")
        self.assertEqual(self.calls["sizing"], 0)

        confirm = f"/api/confirmations/{body['result']['confirmation_id']}/confirm"
        await self.post_json(confirm, {}, expect=404, sub="mallory")  # someone else's confirmation
        result = await self.post_json(confirm, {}, sub="alice")
        self.assertEqual(result["item"]["variant_code"], "CWD-R8-132-4")
        await self.post_json(confirm, {}, expect=409, sub="alice")  # single use

        actions = [a async for a in AuditEvent.objects.order_by("at").values_list("action", "outcome", "sub")]
        self.assertEqual(actions, [
            ("tool:resize_item", "ok", "alice"),
            ("confirm", "denied", "mallory"),
            ("confirm:resize_item", "ok", "alice"),
        ])

    async def test_confirmation_fails_if_item_changed_since_proposal(self):
        args = {"project_id": PROJECT, "item_tag": "DRV-03", "power_kw": 75, "voltage": 400, "load_pct": 130}
        body = await self.call("resize_item", args, sub="alice")
        await self.post_json(
            f"/api/projects/{PROJECT}/items/DRV-03/resize", {"power_kw": 75, "voltage": 400, "load_pct": 110}
        )
        await self.post_json(f"/api/confirmations/{body['result']['confirmation_id']}/confirm", {}, expect=409, sub="alice")

    async def test_unexpected_arguments_are_rejected(self):
        body = await self.call("get_bom_cost", {"project_id": PROJECT, "role": "administrator"}, expect=400)
        self.assertEqual(body["error"], "tool_error")

    async def test_unknown_tool(self):
        await self.call("delete_project", {}, expect=404)
