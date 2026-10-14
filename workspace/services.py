"""Use cases behind the API: sidebar, re-size and project BOM.

Shared by the REST views and the assistant tools, so both go through the same
adapters and therefore the same entitlement checks.
"""

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone

from django.conf import settings
from django.db.models import F

from integrations import registry
from integrations.base import Status
from integrations.context import Context, ItemSnapshot, Principal
from integrations.resilience import UpstreamError

from .models import PlacedItem


class ServiceError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class NotFound(ServiceError):
    status, code = 404, "not_found"


class Conflict(ServiceError):
    status, code = 409, "conflict"


class Unprocessable(ServiceError):
    status, code = 422, "unprocessable"


class Unavailable(ServiceError):
    status, code = 503, "upstream_unavailable"


# --- helpers ---------------------------------------------------------------------


async def load_item(project_id: str, tag: str) -> PlacedItem:
    try:
        return await PlacedItem.objects.select_related("parent").aget(project_id=project_id, tag=tag)
    except PlacedItem.DoesNotExist:
        raise NotFound(f"item {tag} not found in project {project_id}") from None


def snapshot(item: PlacedItem) -> ItemSnapshot:
    return ItemSnapshot(
        id=str(item.id),
        tag=item.tag,
        project_id=item.project_id,
        kind=item.kind,
        variant_code=item.variant_code,
        sizing_inputs=item.sizing_inputs,
        sizing_result=item.sizing_result,
        sized_at=item.sized_at.timestamp() if item.sized_at else None,
    )


def item_summary(item: PlacedItem) -> dict:
    return {
        "id": str(item.id),
        "tag": item.tag,
        "project_id": item.project_id,
        "kind": item.kind,
        "variant_code": item.variant_code,
        "model_ref": item.model_ref,
        "position": item.position,
        "parent": item.parent.tag if item.parent_id else None,
        "sizing_inputs": item.sizing_inputs,
        "revision": item.revision,
    }


def model_ref_from(catalogue_entry: dict) -> dict:
    return {"asset_id": catalogue_entry["model"]["asset_id"], "lods": catalogue_entry["model"]["lods"]}


# --- sidebar -----------------------------------------------------------------------


async def sidebar(project_id: str, tag: str, principal: Principal) -> dict:
    """Fan out to every panel adapter in parallel and answer within the budget.

    Each section carries its own status, so one slow or failing system never
    blanks the others.
    """
    started = time.monotonic()
    deadline = started + settings.SIDEBAR_BUDGET_S
    item = await load_item(project_id, tag)
    ctx = Context(principal, snapshot(item))
    adapters = [a for a in registry.panels() if a.applies_to(ctx)]
    results = await asyncio.gather(*(a.get(ctx, deadline) for a in adapters))
    return {
        "item": item_summary(item),
        "sections": {a.name: r.to_dict() for a, r in zip(adapters, results)},
        "budget_ms": int(settings.SIDEBAR_BUDGET_S * 1000),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


async def sizing_when_ready(project_id: str, tag: str, principal: Principal, timeout_s: float) -> dict:
    """For server push: wait for the in-flight (or a new) sizing run for this item."""
    item = await load_item(project_id, tag)
    ctx = Context(principal, snapshot(item))
    sizing = registry.get("sizing")
    if not sizing.applies_to(ctx):
        raise ServiceError("item has no sizing")
    return (await sizing.get(ctx, time.monotonic() + timeout_s)).to_dict()


# --- re-size -------------------------------------------------------------------------


def validate_inputs(raw: dict) -> dict:
    try:
        inputs = {
            "power_kw": float(raw["power_kw"]),
            "voltage": int(raw["voltage"]),
            "load_pct": float(raw["load_pct"]),
        }
    except (KeyError, TypeError, ValueError):
        raise ServiceError("power_kw, voltage and load_pct are required numbers") from None
    if not 0 < inputs["power_kw"] <= 1000:
        raise ServiceError("power_kw must be in (0, 1000]")
    if inputs["voltage"] not in (400, 690):
        raise ServiceError("voltage must be 400 or 690")
    if not 10 <= inputs["load_pct"] <= 200:
        raise ServiceError("load_pct must be between 10 and 200")
    return inputs


async def resize(project_id: str, tag: str, raw_inputs: dict, principal: Principal, expected_revision=None) -> dict:
    """Re-size a drive. All-or-nothing: the item is only written once sizing and,
    if the variant changes, the new 3D model reference are both known."""
    inputs = validate_inputs(raw_inputs)
    item = await load_item(project_id, tag)
    if item.kind != "drive":
        raise ServiceError("only drives can be re-sized")
    if expected_revision is not None and int(expected_revision) != item.revision:
        raise Conflict(f"item is at revision {item.revision}, not {expected_revision}")

    ctx = Context(principal, snapshot(item).with_inputs(inputs))
    sizing = registry.get("sizing")
    try:
        result = await sizing.call(ctx, time.monotonic() + settings.RESIZE_TIMEOUT_S)
    except UpstreamError as e:
        if e.status == 422:
            raise Unprocessable(f"sizing tool found no variant for these inputs: {e}") from e
        raise Unavailable(f"sizing tool unavailable, item unchanged: {e}") from e
    await sizing.remember(ctx, result)

    old_code, new_code = item.variant_code, result["variant_code"]
    changed = new_code != old_code
    updates = {"sizing_inputs": inputs, "sizing_result": result, "sized_at": datetime.now(timezone.utc)}
    if changed:
        new_ctx = Context(principal, ctx.item.with_variant(new_code))
        entry = await registry.get("catalogue").get(new_ctx, time.monotonic() + 3.0)
        if not entry.usable:
            raise Unavailable("catalogue unavailable, cannot resolve the 3D model for the new variant; item unchanged")
        updates.update(variant_code=new_code, model_ref=model_ref_from(entry.data))

    rows = await PlacedItem.objects.filter(pk=item.pk, revision=item.revision).aupdate(
        **updates, revision=F("revision") + 1
    )
    if rows == 0:
        raise Conflict("item was changed by someone else during the re-size; reload and retry")
    item = await load_item(project_id, tag)

    return {
        "item": item_summary(item),
        "sizing": result,
        "variant_changed": changed,
        "previous_variant": old_code,
        "bom_change": await _bom_change(item, old_code, new_code, principal) if changed else None,
        "warnings": await _fit_warnings(item, principal),
    }


async def _bom_change(item: PlacedItem, old_code: str, new_code: str, principal: Principal) -> dict:
    counts = defaultdict(int)
    async for code in PlacedItem.objects.filter(project_id=item.project_id).values_list("variant_code", flat=True):
        counts[code] += 1
    lines = [
        {"variant_code": old_code, "qty_before": counts[old_code] + 1, "qty_after": counts[old_code], "delta": -1},
        {"variant_code": new_code, "qty_before": counts[new_code] - 1, "qty_after": counts[new_code], "delta": 1},
    ]
    pricing = registry.get("pricing")
    deadline = time.monotonic() + settings.SIDEBAR_BUDGET_S
    old_p, new_p = await asyncio.gather(
        pricing.get(Context(principal, ItemSnapshot.for_variant(old_code)), deadline),
        pricing.get(Context(principal, ItemSnapshot.for_variant(new_code)), deadline),
    )
    if old_p.status == Status.NOT_PERMITTED:
        price = {"status": str(Status.NOT_PERMITTED)}
    elif old_p.usable and new_p.usable and old_p.data["currency"] == new_p.data["currency"]:
        status = Status.OK if old_p.status == new_p.status == Status.OK else Status.STALE
        price = {
            "status": str(status),
            "currency": new_p.data["currency"],
            "delta": round(new_p.data["unit_price"] - old_p.data["unit_price"], 2),
        }
    else:
        price = {"status": str(Status.UNAVAILABLE)}
    return {"lines": lines, "price_change": price}


async def _fit_warnings(item: PlacedItem, principal: Principal) -> list[str]:
    """A bigger frame may no longer fit the cabinet it sits in. Surface it, do not block."""
    if not item.parent_id or not item.sizing_result:
        return []
    ctx = Context(principal, ItemSnapshot.for_variant(item.parent.variant_code))
    cabinet = await registry.get("catalogue").get(ctx, time.monotonic() + 1.0)
    if not cabinet.usable:
        return [f"could not check that {item.tag} still fits {item.parent.tag}: catalogue unavailable"]
    frame = item.sizing_result["frame"]
    if frame not in cabinet.data.get("accepts_frames", []):
        return [f"{item.tag} frame {frame} does not fit cabinet {item.parent.tag} ({item.parent.variant_code})"]
    return []


# --- project BOM ---------------------------------------------------------------------


async def project_bom(project_id: str, principal: Principal) -> dict:
    items = [i async for i in PlacedItem.objects.filter(project_id=project_id)]
    if not items:
        raise NotFound(f"project {project_id} not found or empty")

    groups: dict[str, list[PlacedItem]] = defaultdict(list)
    for i in items:
        groups[i.variant_code].append(i)
    codes = sorted(groups)

    deadline = time.monotonic() + settings.BOM_BUDGET_S
    catalogue, pricing = registry.get("catalogue"), registry.get("pricing")
    ctxs = [Context(principal, ItemSnapshot.for_variant(c)) for c in codes]
    cat_results = await asyncio.gather(*(catalogue.get(c, deadline) for c in ctxs))
    priced = pricing.permitted(principal)
    price_results = await asyncio.gather(*(pricing.get(c, deadline) for c in ctxs)) if priced else [None] * len(codes)

    lines, totals, complete = [], defaultdict(float), True
    longest = None
    for code, cat, price in zip(codes, cat_results, price_results):
        qty = len(groups[code])
        line = {
            "variant_code": code,
            "description": cat.data["name"] if cat.usable else None,
            "kind": groups[code][0].kind,
            "qty": qty,
            "item_tags": sorted(i.tag for i in groups[code]),
            "catalogue_status": str(cat.status),
        }
        if priced:
            line["price_status"] = str(price.status)
            if price.usable:
                line_total = round(price.data["unit_price"] * qty, 2)
                line.update(
                    currency=price.data["currency"],
                    unit_price=price.data["unit_price"],
                    line_total=line_total,
                    lead_time_days=price.data["lead_time_days"],
                    price_valid_until=price.data["valid_until"],
                )
                totals[price.data["currency"]] += line_total
                if longest is None or price.data["lead_time_days"] > longest["lead_time_days"]:
                    longest = {"variant_code": code, "item_tags": line["item_tags"], "lead_time_days": price.data["lead_time_days"]}
            else:
                complete = False
        lines.append(line)

    out = {"project_id": project_id, "lines": lines}
    if priced:
        out["pricing"] = {
            "status": "ok" if complete else "partial",
            "totals": {k: round(v, 2) for k, v in totals.items()},
            "complete": complete,
            "longest_lead_time": longest,
        }
    else:
        # No price, total, currency or lead time keys at all: nothing to hide in the UI.
        out["pricing"] = {"status": str(Status.NOT_PERMITTED)}
    return out
