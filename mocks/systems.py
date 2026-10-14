"""Mock enterprise systems: catalogue, sizing tool and ERP pricing.

They are written as one httpx-compatible handler so the same code serves two
purposes: the Django catch-all view exposes them over real HTTP for local runs,
and the tests plug `handle` straight into httpx.MockTransport. Each mock only
knows its own data, like the real systems would.
"""

import asyncio
import json
import random
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from django.conf import settings

DATA_DIR = Path(__file__).parent / "data"
CATALOGUE = {v["variant_code"]: v for v in json.loads((DATA_DIR / "catalogue.json").read_text())["variants"]}
PRICES = json.loads((DATA_DIR / "prices.json").read_text())
SIZING_TOOL_VERSION = "2.4.1"

_rng = random.Random()
_pricing_calls: deque[float] = deque()


def reset():
    _pricing_calls.clear()


def _json(status, body, headers=None):
    return httpx.Response(status, json=body, headers=headers)


async def handle(request: httpx.Request) -> httpx.Response:
    parts = request.url.path.strip("/").split("/")
    if parts and parts[0] == "mock":
        parts = parts[1:]
    system, rest = (parts[0], parts[1:]) if parts else ("", [])
    route = {"catalogue": _catalogue, "sizing": _sizing, "pricing": _pricing}.get(system)
    if route is None:
        return _json(404, {"error": "unknown_system"})
    return await route(request, rest)


# --- Catalogue: static JSON ---------------------------------------------------------


async def _catalogue(request, rest):
    if rest[:2] == ["v1", "variants"] and len(rest) == 3:
        variant = CATALOGUE.get(rest[2])
        return _json(200, variant) if variant else _json(404, {"error": "variant_not_found"})
    if rest == ["v1", "variants"]:
        q = request.url.params.get("q", "").lower()
        hits = [v for v in CATALOGUE.values() if q in v["variant_code"].lower() or q in v["name"].lower()]
        return _json(200, {"items": hits[:50], "total": len(hits)})
    return _json(404, {"error": "not_found"})


# --- Sizing tool: slow, flaky, needs the user's delegated token ------------------


async def _sizing(request, rest):
    if request.method != "POST" or rest != ["v2", "runs"]:
        return _json(404, {"error": "not_found"})
    if not request.headers.get("authorization", "").startswith("Bearer "):
        return _json(401, {"error": "missing_delegated_token"})

    conf = settings.MOCK_SIZING
    lo, hi = conf["latency_s"]
    await asyncio.sleep(_rng.uniform(lo, hi))
    if _rng.random() < conf["failure_rate"]:
        return _json(503, {"error": "sizing_engine_unavailable"})

    try:
        body = json.loads(request.content)
        power_kw = float(body["motor"]["power_kw"])
        voltage = int(body["motor"]["voltage"])
        load_pct = float(body["load_pct"])
    except (ValueError, KeyError, TypeError):
        return _json(400, {"error": "invalid_request"})

    required_kw = power_kw * load_pct / 100
    candidates = sorted(
        (v for v in CATALOGUE.values() if v["kind"] == "drive" and v["voltage"] == voltage and v["rated_kw"] >= required_kw),
        key=lambda v: v["rated_kw"],
    )
    if not candidates:
        return _json(422, {"error": "no_variant_fits", "required_kw": required_kw})
    best = candidates[0]
    return _json(200, {
        "run_id": str(uuid.uuid4()),
        "tool_version": SIZING_TOOL_VERSION,
        "recommendation": {
            "variant_code": best["variant_code"],
            "frame": best["frame"],
            "rated_kw": best["rated_kw"],
        },
        "required_kw": round(required_kw, 2),
        "utilisation_pct": round(100 * required_kw / best["rated_kw"], 1),
    })


# --- ERP pricing: rate limited, priced by country and customer group ------------


async def _pricing(request, rest):
    if rest != ["v1", "prices"]:
        return _json(404, {"error": "not_found"})

    now = time.monotonic()
    while _pricing_calls and now - _pricing_calls[0] >= 1.0:
        _pricing_calls.popleft()
    if len(_pricing_calls) >= settings.MOCK_PRICING["rate_per_s"]:
        return _json(429, {"error": "rate_limited"}, headers={"Retry-After": "1"})
    _pricing_calls.append(now)

    params = request.url.params
    code, country, group = params.get("variant"), params.get("country"), params.get("customer_group", "standard")
    price, market = PRICES["variants"].get(code), PRICES["countries"].get(country)
    if price is None:
        return _json(404, {"error": "variant_not_priced"})
    if market is None:
        return _json(404, {"error": "no_price_list_for_country"})
    group_factor = PRICES["customer_groups"].get(group, 1.0)
    unit_price = round(price["base_eur"] * market["fx"] * market["factor"] * group_factor, 2)
    return _json(200, {
        "variant_code": code,
        "country": country,
        "customer_group": group,
        "currency": market["currency"],
        "unit_price": unit_price,
        "lead_time_days": price["lead_time_days"],
        "valid_until": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    })
