from ..base import Adapter
from ..context import Context
from ..registry import register


@register
class CatalogueAdapter(Adapter):
    """Technical data and 3D model references. Shared cache: catalogue data is the same for everyone."""

    name = "catalogue"
    panel_order = 20

    def cache_key(self, ctx: Context) -> str:
        return ctx.item.variant_code

    async def fetch(self, ctx: Context, timeout: float) -> dict:
        data = await self.request("GET", f"/v1/variants/{ctx.item.variant_code}", timeout=timeout)
        self.require(data, "variant_code", "kind", "name", "model.asset_id", "model.lods")
        return data
