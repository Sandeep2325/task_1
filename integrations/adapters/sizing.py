import hashlib
import json

from ..base import Adapter
from ..context import Context
from ..registry import register


def inputs_key(inputs: dict) -> str:
    canonical = json.dumps(
        {"power_kw": float(inputs["power_kw"]), "voltage": int(inputs["voltage"]), "load_pct": float(inputs["load_pct"])},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


@register
class SizingAdapter(Adapter):
    """Product selection and sizing tool: slow, flaky, rate limited, called with the user's token.

    Results depend only on the motor/load inputs, so the cache is shared and keyed
    by a hash of those inputs. The last result is also persisted on the item, which
    is the fallback when the tool is down and the cache has expired.
    """

    name = "sizing"
    panel_order = 10

    def applies_to(self, ctx: Context) -> bool:
        return ctx.item.kind == "drive" and bool(ctx.item.sizing_inputs)

    def cache_key(self, ctx: Context) -> str:
        return inputs_key(ctx.item.sizing_inputs)

    async def fetch(self, ctx: Context, timeout: float) -> dict:
        inputs = ctx.item.sizing_inputs
        data = await self.request(
            "POST",
            "/v2/runs",
            timeout=timeout,
            headers={"Authorization": f"Bearer {ctx.principal.token}"},
            json={
                "motor": {"power_kw": inputs["power_kw"], "voltage": inputs["voltage"]},
                "load_pct": inputs["load_pct"],
            },
        )
        self.require(data, "recommendation.variant_code", "recommendation.frame", "utilisation_pct", "tool_version")
        rec = data["recommendation"]
        return {
            "inputs": inputs,
            "variant_code": rec["variant_code"],
            "frame": rec["frame"],
            "rated_kw": rec.get("rated_kw"),
            "required_kw": data.get("required_kw"),
            "utilisation_pct": data["utilisation_pct"],
            "tool_version": data["tool_version"],
        }

    def fallback(self, ctx: Context):
        item = ctx.item
        if item.sizing_result and item.sized_at and inputs_key(item.sizing_result["inputs"]) == self.cache_key(ctx):
            return item.sizing_result, item.sized_at
        return None
