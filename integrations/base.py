"""The adapter contract and the resilience pipeline every adapter shares.

An adapter implements four small things: `permitted`, `applies_to`,
`cache_key` and `fetch`. Everything else (entitlement gate, cache with TTL and
stale window, single-flight, rate limit, retries with backoff, circuit breaker,
deadline, fallback) is inherited, so a new system cannot forget any of it.
"""

import asyncio
import enum
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, ClassVar

import httpx
from django.conf import settings
from django.core.cache import cache

from . import http
from .context import Context, Principal
from .resilience import (
    CircuitBreaker,
    CircuitOpen,
    ContractError,
    DeadlineExceeded,
    RateLimited,
    TokenBucket,
    UpstreamError,
    backoff_delay,
)

log = logging.getLogger(__name__)


class Status(enum.StrEnum):
    OK = "ok"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    NOT_PERMITTED = "not_permitted"


@dataclass
class SectionResult:
    status: Status
    data: dict[str, Any] | None = None
    fetched_at: str | None = None  # ISO 8601, when the upstream produced the data
    pending: bool = False  # a fresher result is still being fetched (see the SSE stream)
    error: str | None = None

    def to_dict(self):
        out = asdict(self)
        out["status"] = str(self.status)
        return out

    @property
    def usable(self) -> bool:
        return self.status in (Status.OK, Status.STALE)


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None


class Adapter:
    name: ClassVar[str]
    panel_order: ClassVar[int] = 100
    shows_in_sidebar: ClassVar[bool] = True

    def __init__(self):
        self.reset_state()

    # --- what each adapter provides ---------------------------------------------

    def permitted(self, principal: Principal) -> bool:
        return True

    def applies_to(self, ctx: Context) -> bool:
        return True

    def cache_key(self, ctx: Context) -> str:
        raise NotImplementedError

    async def fetch(self, ctx: Context, timeout: float) -> dict[str, Any]:
        """One upstream attempt. Raise UpstreamError on failure; return normalised data."""
        raise NotImplementedError

    def stale_usable(self, data: dict[str, Any]) -> bool:
        return True

    def fallback(self, ctx: Context) -> tuple[dict[str, Any], float] | None:
        """Last-known data from somewhere other than the cache, as (data, fetched_at)."""
        return None

    # --- configuration and per-process state ---------------------------------------

    @property
    def conf(self) -> dict[str, Any]:
        return settings.INTEGRATIONS[self.name]

    def reset_state(self):
        self._bucket: TokenBucket | None = None
        self._breaker: CircuitBreaker | None = None
        self._inflight: dict[str, asyncio.Task] = {}

    @property
    def bucket(self) -> TokenBucket | None:
        if self._bucket is None and "rate_per_s" in self.conf:
            self._bucket = TokenBucket(self.conf["rate_per_s"], self.conf.get("burst", self.conf["rate_per_s"]))
        return self._bucket

    @property
    def breaker(self) -> CircuitBreaker | None:
        if self._breaker is None and "breaker" in self.conf:
            self._breaker = CircuitBreaker(**self.conf["breaker"])
        return self._breaker

    # --- HTTP helper -------------------------------------------------------------

    async def request(self, method: str, path: str, *, timeout: float, **kwargs) -> dict[str, Any]:
        async with http.client(self.conf["base_url"], timeout) as c:
            r = await c.request(method, path, **kwargs)
        if r.status_code == 429:
            retry_after = float(r.headers.get("Retry-After", "1"))
            raise UpstreamError(f"{self.name}: rate limited by upstream", retryable=True, retry_after=retry_after, status=429)
        if r.status_code >= 500:
            raise UpstreamError(f"{self.name}: upstream {r.status_code}", retryable=True, outage=True, status=r.status_code)
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", r.text)
            except ValueError:
                detail = r.text
            raise UpstreamError(f"{self.name}: {detail}", status=r.status_code)
        try:
            return r.json()
        except ValueError as e:
            raise ContractError(f"{self.name}: response is not JSON") from e

    @staticmethod
    def require(data: dict, *paths: str) -> None:
        """Fail loudly when a vendor response loses a field we depend on."""
        for p in paths:
            node = data
            for part in p.split("."):
                if not isinstance(node, dict) or part not in node:
                    raise ContractError(f"response missing '{p}'")
                node = node[part]

    # --- pipeline ----------------------------------------------------------------

    async def call(self, ctx: Context, deadline: float) -> dict[str, Any]:
        """Fresh data from upstream with breaker, rate limit and retries, bounded by `deadline`."""
        retries = self.conf.get("retries", 0)
        attempt = 0
        while True:
            attempt += 1
            if self.breaker and not self.breaker.allow():
                raise CircuitOpen(self.name)
            if self.bucket:
                try:
                    wait = self.bucket.reserve(deadline)
                except RateLimited:
                    raise RateLimited(self.name) from None
                if wait:
                    await asyncio.sleep(wait)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeadlineExceeded(self.name)
            try:
                data = await asyncio.wait_for(self.fetch(ctx, timeout=remaining), remaining)
            except UpstreamError as e:
                err = e
            except (TimeoutError, httpx.TimeoutException):
                err = UpstreamError(f"{self.name}: timed out", retryable=True, outage=True)
            except httpx.TransportError as e:
                err = UpstreamError(f"{self.name}: {type(e).__name__}", retryable=True, outage=True)
            else:
                if self.breaker:
                    self.breaker.record_success()
                return data

            if isinstance(err, ContractError):
                log.error("contract violation from %s: %s", self.name, err)
            if self.breaker:
                if err.outage:
                    self.breaker.record_failure()
                else:
                    self.breaker.record_success()  # it answered; release a half-open probe
            if not err.retryable or attempt > retries:
                raise err
            delay = backoff_delay(attempt, **settings.RETRY_BACKOFF)
            if err.retry_after:
                delay = max(delay, err.retry_after)
            if time.monotonic() + delay >= deadline:
                raise err
            log.info("retrying %s after %.2fs (attempt %d): %s", self.name, delay, attempt, err)
            await asyncio.sleep(delay)

    async def remember(self, ctx: Context, data: dict[str, Any]) -> float:
        fetched_at = time.time()
        await cache.aset(self._ckey(ctx), {"data": data, "fetched_at": fetched_at}, timeout=self.conf["stale_ttl_s"])
        return fetched_at

    async def get(self, ctx: Context, deadline: float) -> SectionResult:
        """Best answer available by `deadline`. Never raises."""
        if not self.permitted(ctx.principal):
            return SectionResult(Status.NOT_PERMITTED)
        try:
            key = self._ckey(ctx)
            cached = await cache.aget(key)
            if cached and time.time() - cached["fetched_at"] < self.conf["ttl_s"]:
                return SectionResult(Status.OK, cached["data"], _iso(cached["fetched_at"]))

            pending, error = False, None
            try:
                data, fetched_at = await asyncio.wait_for(
                    asyncio.shield(self._refresh(ctx, key)), max(0.0, deadline - time.monotonic())
                )
                return SectionResult(Status.OK, data, _iso(fetched_at))
            except TimeoutError:
                pending, error = True, "still loading"
            except UpstreamError as e:
                error = str(e)

            if cached and self.stale_usable(cached["data"]):
                return SectionResult(Status.STALE, cached["data"], _iso(cached["fetched_at"]), pending, error)
            fb = self.fallback(ctx)
            if fb:
                return SectionResult(Status.STALE, fb[0], _iso(fb[1]), pending, error)
            return SectionResult(Status.UNAVAILABLE, pending=pending, error=error)
        except Exception:
            log.exception("adapter %s failed", self.name)
            return SectionResult(Status.UNAVAILABLE, error="internal error")

    def _ckey(self, ctx: Context) -> str:
        return f"cw:{self.name}:{self.cache_key(ctx)}"

    def _refresh(self, ctx: Context, key: str) -> asyncio.Task:
        """Single-flight: concurrent callers for the same key share one upstream call.

        The task has its own deadline (`call_timeout_s`), independent of the
        caller's budget, so a slow sizing run still lands in the cache after the
        sidebar has answered without it.
        """
        task = self._inflight.get(key)
        if task is None:
            async def run():
                data = await self.call(ctx, time.monotonic() + self.conf["call_timeout_s"])
                return data, await self.remember(ctx, data)

            task = asyncio.ensure_future(run())
            self._inflight[key] = task
            task.add_done_callback(lambda t: self._finished(key, t))
        return task

    def _finished(self, key: str, task: asyncio.Task):
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled() and task.exception():
            log.warning("%s refresh failed: %s", self.name, task.exception())

    async def drain(self):
        tasks = list(self._inflight.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
