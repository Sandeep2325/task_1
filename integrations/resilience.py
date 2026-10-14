"""Retry, rate limiting and circuit breaking for upstream calls.

All state is per process. With several replicas the rate limit has to move to
Redis (a shared token bucket); the design document covers that step.
"""

import random
import time


class UpstreamError(Exception):
    """An upstream call failed.

    retryable: worth trying again within the deadline.
    outage: counts against the circuit breaker (5xx, timeouts, connection errors).
    """

    def __init__(self, message, *, retryable=False, outage=False, retry_after=None, status=None):
        super().__init__(message)
        self.retryable = retryable
        self.outage = outage
        self.retry_after = retry_after
        self.status = status


class CircuitOpen(UpstreamError):
    def __init__(self, name):
        super().__init__(f"{name}: circuit open, not calling upstream")


class RateLimited(UpstreamError):
    def __init__(self, name):
        super().__init__(f"{name}: local rate limit would exceed the deadline")


class DeadlineExceeded(UpstreamError):
    def __init__(self, name):
        super().__init__(f"{name}: deadline exceeded")


class ContractError(UpstreamError):
    """The upstream answered, but not in the shape we expect: a vendor API change."""


class TokenBucket:
    """Rate limiter that hands out reservations instead of blocking on a lock.

    Tokens may go negative: each caller reserves a slot in the future and sleeps
    until then, which gives FIFO ordering without a lock. A caller whose slot is
    past its deadline is refused immediately, so it can fall back to stale data
    instead of queueing behind everyone else.
    """

    def __init__(self, rate_per_s: float, burst: float, clock=time.monotonic):
        self.rate = rate_per_s
        self.capacity = burst
        self.tokens = burst
        self.clock = clock
        self.updated = clock()

    def reserve(self, deadline: float) -> float:
        """Return seconds to wait before calling, or raise if that is past the deadline."""
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        wait = 0.0 if self.tokens >= 1 else (1 - self.tokens) / self.rate
        if now + wait > deadline:
            raise RateLimited("bucket")
        self.tokens -= 1
        return wait


class CircuitBreaker:
    """Closed -> open after N consecutive outage failures -> half-open after a cool-down.

    In half-open state exactly one probe call is let through; its outcome closes
    or re-opens the circuit.
    """

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int, reset_after_s: float, clock=time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_after_s = reset_after_s
        self.clock = clock
        self.state = self.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self.probe_in_flight = False

    def allow(self) -> bool:
        if self.state == self.OPEN and self.clock() - self.opened_at >= self.reset_after_s:
            self.state = self.HALF_OPEN
            self.probe_in_flight = False
        if self.state == self.CLOSED:
            return True
        if self.state == self.HALF_OPEN and not self.probe_in_flight:
            self.probe_in_flight = True
            return True
        return False

    def record_success(self):
        self.state = self.CLOSED
        self.failures = 0
        self.probe_in_flight = False

    def record_failure(self):
        self.failures += 1
        if self.state == self.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = self.OPEN
            self.opened_at = self.clock()
            self.probe_in_flight = False


def backoff_delay(attempt: int, base_s: float, max_s: float) -> float:
    """Exponential backoff with full jitter (attempt starts at 1)."""
    return random.uniform(0, min(max_s, base_s * 2 ** (attempt - 1)))
