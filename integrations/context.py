"""Who is asking and about what. Adapters see only these two types, never the ORM."""

from dataclasses import dataclass, field, replace
from typing import Any

SALES_ENGINEER = "sales_engineer"
CHANNEL_PARTNER = "channel_partner"
ADMINISTRATOR = "administrator"

# The single place that decides price visibility. Enforced by the pricing
# adapter before any cache read or upstream call, so it holds for the REST API
# and for assistant tools alike.
PRICE_VISIBLE_ROLES = frozenset({SALES_ENGINEER, ADMINISTRATOR})


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str
    country: str
    customer_group: str = "standard"
    token: str = field(default="", repr=False)  # forwarded as the delegated token

    @property
    def can_see_prices(self) -> bool:
        return self.role in PRICE_VISIBLE_ROLES


@dataclass(frozen=True)
class ItemSnapshot:
    """A placed item as the adapters need it. `id` never changes, even when a re-size swaps the variant."""

    id: str
    tag: str
    project_id: str
    kind: str
    variant_code: str
    sizing_inputs: dict[str, Any] | None = None
    sizing_result: dict[str, Any] | None = None
    sized_at: float | None = None

    @classmethod
    def for_variant(cls, variant_code: str, kind: str = "") -> "ItemSnapshot":
        return cls(id="", tag="", project_id="", kind=kind, variant_code=variant_code)

    def with_inputs(self, inputs: dict[str, Any]) -> "ItemSnapshot":
        return replace(self, sizing_inputs=inputs)

    def with_variant(self, variant_code: str) -> "ItemSnapshot":
        return replace(self, variant_code=variant_code)


@dataclass(frozen=True)
class Context:
    principal: Principal
    item: ItemSnapshot
