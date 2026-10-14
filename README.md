# Cloudworx sidebar integration service (Part B)

A small Django (async) service that feeds the workspace sidebar from three mocked
enterprise systems: a slow, flaky **sizing tool**, a static-JSON **catalogue** and
a rate-limited **ERP pricing** service. Each sidebar section carries its own status
(`ok`, `stale`, `unavailable`, `not_permitted`), so the panel can render partial
results inside the 1 s budget.

## Run it

Python 3.12 or newer (Django 6.1).

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt          # Windows: .venv\Scripts\pip
```

```bash
.venv/bin/python manage.py migrate && .venv/bin/python manage.py seed_demo
```

```bash
.venv/bin/uvicorn config.asgi:application --port 8000
```

Run it under **uvicorn (ASGI)**, not `runserver`: a slow sizing run keeps going in
the background after the sidebar has answered, and that needs a long-lived event loop.

The mock systems are served by the same process under `/mock/`. If you use a port
other than 8000, set `MOCK_BASE_URL=http://127.0.0.1:<port>/mock` and
`OIDC_ISSUER=http://127.0.0.1:<port>/mock/oidc`.

Or with Docker:

```bash
docker build -t cloudworx-integrations . && docker run -p 8000:8000 cloudworx-integrations
```

### Try it

```bash
TOKEN=$(curl -s -X POST localhost:8000/mock/oidc/token -H 'content-type: application/json' -d '{"role":"sales_engineer","country":"DE"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/projects/P-24117/items/DRV-03/sidebar
```

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{"power_kw":75,"voltage":400,"load_pct":130}' localhost:8000/api/projects/P-24117/items/DRV-03/resize
```

```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/projects/P-24117/bom
```

Use `"role":"channel_partner"` for a user who must never see prices, and
`"country":"IN"` (or `US`, `GB`, `FR`) for another price list.
`python manage.py issue_token --role channel_partner` prints a token too.

### Tests

```bash
.venv/bin/python manage.py test
```

49 tests, about 7 s, no network. Adapters talk to the real mock code through
`httpx.MockTransport`, so retries, status codes and headers are all exercised.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/projects/{id}` | Layout: placed items with variant, 3D model ref, revision |
| GET | `/api/projects/{id}/items/{tag}/sidebar` | All panels for one item, within `SIDEBAR_BUDGET_S` (0.9 s) |
| GET | `/api/projects/{id}/items/{tag}/sizing/stream` | SSE: pushes the sizing result when a slow run lands |
| POST | `/api/projects/{id}/items/{tag}/resize` | `{power_kw, voltage, load_pct, expected_revision?}` → new variant, model ref, BOM change |
| GET | `/api/projects/{id}/bom` | BOM with prices filtered by the caller's role and country |
| GET | `/api/assistant/tools` | Tool definitions for the LLM (JSON Schema) |
| POST | `/api/assistant/tools/{name}` | Invoke a tool as the calling user |
| POST | `/api/confirmations/{id}/confirm` | User confirms a write the assistant proposed |

The sidebar contract, per section:

```json
{"status": "stale", "data": {...}, "fetched_at": "2026-10-14T15:20:11+00:00", "pending": true, "error": "sizing: upstream 503"}
```

`pending: true` means a fresher result is on its way; the panel opens the SSE stream.
A section is omitted when it does not apply (a cabinet has no sizing panel).

## How it is built

```
integrations/          the integration layer, no Django views
  base.py              Adapter contract + shared pipeline (entitlement → cache → single-flight →
                       rate limit → breaker → retries → deadline → stale/fallback)
  resilience.py        TokenBucket, CircuitBreaker, backoff, error types
  registry.py          adapters register themselves; the sidebar iterates the registry
  adapters/            one file per system: catalogue.py, sizing.py, pricing.py
  context.py           Principal (who) and ItemSnapshot (what); price-visibility policy
workspace/             projects and placed items, services (sidebar, resize, BOM), REST views, JWT checks
assistant/             tool definitions, confirmation flow, audit log
mocks/                 the three mock systems + a mock OIDC issuer (dev only)
tests/
```

**Per-system policy** is in `config/settings.py` → `INTEGRATIONS`:

| System | Fresh TTL | Serve stale up to | Rate limit | Retries | Breaker |
|---|---|---|---|---|---|
| Catalogue | 15 min | 24 h | — | 2 | — |
| Sizing | 6 h (keyed by inputs) | 7 days, then the result persisted on the item | 20/s | 2 | 5 failures → open 30 s |
| Pricing | 5 min (keyed by variant, country, customer group) | until the quote's `valid_until` | 5/s | 2 (honours `Retry-After`) | — |

Key decisions:

- **Entitlements are enforced in the adapter, before the cache.** The pricing adapter
  refuses a channel partner before it reads the cache or calls the ERP, so the
  REST API and assistant tools get the same guarantee. A partner's BOM has no price,
  total, currency or lead-time keys at all.
- **Shared caches, never per user.** Catalogue by variant, sizing by an input hash,
  price by variant, country and customer group. That is safe because the permission
  check runs first.
- **Budget, not timeouts.** The sidebar waits at most 0.9 s. A slow upstream call is
  not cancelled. It finishes in the background (single-flight, so 600 users clicking
  the same drive cause one sizing run), fills the cache and is pushed over SSE.
- **The rate limiter refuses instead of queueing past the deadline.** A caller whose
  slot would land after its budget gets `stale` or `unavailable` immediately.
- **Item identity is stable.** `PlacedItem.id` never changes. A re-size swaps
  `variant_code` and `model_ref` in one conditional update on `revision`. Nothing is
  written unless both the sizing result and the new 3D model reference are known.
  The response flags a frame that no longer fits its cabinet.
- **Vendor API changes fail loudly.** Adapters check the response fields they depend
  on (`ContractError`) instead of passing `KeyError`s to users.
- **Assistant writes need a human.** `resize_item` only stores a `PendingAction` bound
  to the user, the item revision and a 5-minute expiry. The user confirms through a
  UI endpoint that is not a tool. Every tool call and confirmation is audited. Tool
  output is labelled as untrusted data.

## Assumptions and shortcuts

- **Mocks share the process.** The mocks run in the same process under `/mock/`, so
  the service is one command. Adapters still call them over HTTP.
- **Per-process state.** The rate limiter, circuit breaker and in-flight map live in
  each process. With several replicas they move to Redis. The cache already does when
  `REDIS_URL` is set.
- **Local working copy of projects.** SQLite holds a working copy of the host
  project. In the real system the host is the source of truth, and layout writes go
  back to it.
- **Lead time is gated with price.** Lead time comes from the same ERP call as price,
  so it is treated as restricted too. A field-level policy could show it to partners.
- **The delegated token is forwarded as-is.** The sizing mock receives the user's
  bearer token. In production this would be an on-behalf-of token exchange for the
  sizing tool's audience.
- **Dev-only token issuer.** `/mock/oidc/token` mints tokens for any role so the API
  can be tried. It is only mounted when `MOCKS_ENABLED` is true.

## AI tools used

Built with Claude Code (Claude Opus) as a pair programmer. I wrote the requirements
and design decisions (adapter pipeline, per-section statuses, entitlement-before-cache,
confirmed writes). Claude drafted code and tests to that design, and I reviewed,
ran and corrected them. For example, the pricing burst was lowered to 1 so the
client-side bucket can never exceed the ERP's 5-per-second window.
