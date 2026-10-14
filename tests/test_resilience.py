from django.test import SimpleTestCase

from integrations.resilience import CircuitBreaker, RateLimited, TokenBucket


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TokenBucketTests(SimpleTestCase):
    def test_reservations_are_spaced_by_the_rate(self):
        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=5, burst=1, clock=clock)
        waits = [bucket.reserve(deadline=10) for _ in range(4)]
        self.assertEqual([round(w, 3) for w in waits], [0.0, 0.2, 0.4, 0.6])

    def test_refuses_when_the_wait_exceeds_the_deadline(self):
        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=5, burst=1, clock=clock)
        bucket.reserve(deadline=1)
        with self.assertRaises(RateLimited):
            bucket.reserve(deadline=0.1)
        self.assertEqual(round(bucket.reserve(deadline=1), 3), 0.2)  # a refusal does not consume a slot

    def test_refills_over_time(self):
        clock = FakeClock()
        bucket = TokenBucket(rate_per_s=5, burst=1, clock=clock)
        bucket.reserve(deadline=1)
        clock.now = 1.0
        self.assertEqual(bucket.reserve(deadline=2), 0.0)


class CircuitBreakerTests(SimpleTestCase):
    def test_opens_after_threshold_and_half_opens_after_cooldown(self):
        clock = FakeClock()
        breaker = CircuitBreaker(failure_threshold=3, reset_after_s=30, clock=clock)
        for _ in range(3):
            self.assertTrue(breaker.allow())
            breaker.record_failure()
        self.assertEqual(breaker.state, "open")
        self.assertFalse(breaker.allow())

        clock.now = 30
        self.assertTrue(breaker.allow())  # one probe
        self.assertFalse(breaker.allow())  # and only one
        breaker.record_success()
        self.assertEqual(breaker.state, "closed")

    def test_failed_probe_reopens(self):
        clock = FakeClock()
        breaker = CircuitBreaker(failure_threshold=1, reset_after_s=10, clock=clock)
        breaker.record_failure()
        clock.now = 10
        self.assertTrue(breaker.allow())
        breaker.record_failure()
        self.assertEqual(breaker.state, "open")
        clock.now = 15
        self.assertFalse(breaker.allow())

    def test_success_resets_the_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=3, reset_after_s=10)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        self.assertEqual(breaker.state, "closed")
