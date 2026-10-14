from datetime import datetime, timezone

from ..base import Adapter
from ..context import Context, Principal
from ..registry import register


@register
class PricingAdapter(Adapter):
    """ERP price and lead time. Cache is shared per (variant, country, customer group),
    never per user, and the entitlement check runs before the cache is touched."""

    name = "pricing"
    panel_order = 30

    def permitted(self, principal: Principal) -> bool:
        return principal.can_see_prices

    def cache_key(self, ctx: Context) -> str:
        p = ctx.principal
        return f"{ctx.item.variant_code}:{p.country}:{p.customer_group}"

    async def fetch(self, ctx: Context, timeout: float) -> dict:
        p = ctx.principal
        data = await self.request(
            "GET",
            "/v1/prices",
            timeout=timeout,
            params={"variant": ctx.item.variant_code, "country": p.country, "customer_group": p.customer_group},
        )
        self.require(data, "currency", "unit_price", "lead_time_days", "valid_until")
        return {k: data[k] for k in ("variant_code", "country", "currency", "unit_price", "lead_time_days", "valid_until")}

    def stale_usable(self, data: dict) -> bool:
        # A quote is valid for 24 h; never show an expired price, even as stale.
        return datetime.fromisoformat(data["valid_until"]) > datetime.now(timezone.utc)
