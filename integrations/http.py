"""Outbound HTTP for adapters.

Tests swap the transport for httpx.MockTransport(mocks.systems.handle), so the
whole adapter stack (headers, status codes, retries) runs without a network.
"""

import httpx

_transport: httpx.AsyncBaseTransport | None = None


def use_transport(transport: httpx.AsyncBaseTransport | None):
    global _transport
    _transport = transport


def client(base_url: str, timeout: float) -> httpx.AsyncClient:
    # One client per call keeps event-loop ownership simple. A pooled client per
    # worker is the production choice; see the design document.
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=max(timeout, 0.01),
        transport=_transport,
        headers={"User-Agent": "cloudworx-integrations/0.1"},
    )
