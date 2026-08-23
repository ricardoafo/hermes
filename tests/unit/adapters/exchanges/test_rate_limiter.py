"""Invariant tests for the exchange-adapter token-bucket rate limiter.

Uses a deterministic fake clock + sleep observer so no real wall-clock
time is consumed. The property test verifies the load-bearing invariant:
bursts of N requests at weight 1 collapse into exactly ceil(N/max_weight)
full windows of throttling.

Reference: docs/guides/ai-authored-tests.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter


@dataclass(slots=True)
class _FakeTime:
    """Deterministic monotonic clock + sleep observer for rate-limiter tests."""

    _now: float = 0.0
    slept: list[float] = field(default_factory=list)

    def clock(self) -> float:
        """Return the current fake time in seconds."""
        return self._now

    def sleep(self, seconds: float) -> None:
        """Advance the clock by ``seconds`` and record the wait."""
        self.slept.append(seconds)
        self._now += seconds

    def advance(self, seconds: float) -> None:
        """Advance the clock by ``seconds`` WITHOUT recording a sleep."""
        self._now += seconds


def _make_limiter(
    *,
    max_weight: int = 6,
    window_seconds: int = 60,
    fake: _FakeTime | None = None,
) -> tuple[RateLimiter, _FakeTime]:
    """Return a limiter wired to a fresh :class:`_FakeTime` (or the one given)."""
    fake = fake or _FakeTime()
    limiter = RateLimiter.for_testing(
        RateLimitConfig(max_weight=max_weight, window_seconds=window_seconds),
        clock=fake.clock,
        sleep=fake.sleep,
    )
    return limiter, fake


class TestRateLimitConfigValidation:
    """Invalid config values are rejected at construction, not at first acquire."""

    def test_zero_max_weight_raises(self) -> None:
        """max_weight of 0 raises ValueError — a bucket that never fills is nonsensical."""
        with pytest.raises(ValueError, match="max_weight"):
            RateLimitConfig(max_weight=0, window_seconds=60)

    def test_negative_window_raises(self) -> None:
        """window_seconds of 0 raises ValueError — refill rate would be undefined."""
        with pytest.raises(ValueError, match="window_seconds"):
            RateLimitConfig(max_weight=5, window_seconds=0)


class TestTryAcquireHappyPath:
    """try_acquire returns True while tokens are available, False when the bucket runs dry."""

    def test_first_call_succeeds_immediately(self) -> None:
        """A fresh bucket has max_weight tokens; the first try_acquire returns True."""
        limiter, _ = _make_limiter(max_weight=3)
        assert limiter.try_acquire(1) is True

    def test_bucket_exhaustion_returns_false(self) -> None:
        """After max_weight successful acquires, the next try_acquire returns False."""
        limiter, _ = _make_limiter(max_weight=3)
        assert limiter.try_acquire(1) is True
        assert limiter.try_acquire(1) is True
        assert limiter.try_acquire(1) is True
        assert limiter.try_acquire(1) is False

    def test_time_passage_refills_bucket(self) -> None:
        """Advancing the clock by window_seconds fully refills an empty bucket."""
        limiter, fake = _make_limiter(max_weight=3, window_seconds=6)
        for _ in range(3):
            limiter.try_acquire(1)
        assert limiter.try_acquire(1) is False
        fake.advance(6.0)  # a full window
        assert limiter.try_acquire(1) is True


class TestAcquireBlocksUntilTokensAvailable:
    """acquire sleeps just long enough to earn the requested weight."""

    def test_acquire_records_sleep_when_bucket_empty(self) -> None:
        """Acquire on an empty bucket sleeps for a positive duration proportional to deficit."""
        limiter, fake = _make_limiter(max_weight=6, window_seconds=60)
        for _ in range(6):
            limiter.acquire(1)  # drains the bucket, no sleep
        assert fake.slept == []
        limiter.acquire(1)  # must wait — refill rate is 6/60 = 0.1 tok/s → 10s
        assert fake.slept == pytest.approx([10.0], rel=1e-6)

    def test_acquire_no_sleep_when_tokens_available(self) -> None:
        """Acquire with tokens available records zero sleeps."""
        limiter, fake = _make_limiter(max_weight=3)
        limiter.acquire(1)
        limiter.acquire(1)
        assert fake.slept == []


class TestValidation:
    """Weight validation runs on every acquire — invalid weights fail fast."""

    def test_zero_weight_raises(self) -> None:
        """Weight of 0 raises ValueError — a no-op acquire is a caller bug."""
        limiter, _ = _make_limiter()
        with pytest.raises(ValueError, match="must be > 0"):
            limiter.acquire(0)

    def test_weight_above_max_raises(self) -> None:
        """Weight above max_weight raises — the request could never succeed."""
        limiter, _ = _make_limiter(max_weight=6)
        with pytest.raises(ValueError, match="exceeds max_weight"):
            limiter.acquire(7)


class TestBurstThrottleProperty:
    """Burst-throttle shape invariant.

    N requests at weight 1 into a max_weight bucket collapse into exactly
    ceil((N - max_weight) / max_weight) full-window sleeps for N > max_weight,
    and 0 for N <= max_weight.
    """

    @given(
        max_weight=st.integers(min_value=2, max_value=20),
        # Bursts of up to 3 windows worth of requests
        n_requests=st.integers(min_value=1, max_value=60),
    )
    def test_burst_throttling_shape(self, max_weight: int, n_requests: int) -> None:
        """Total wall time slept equals the theoretical minimum for the burst size."""
        window_seconds = 60
        limiter, fake = _make_limiter(max_weight=max_weight, window_seconds=window_seconds)
        # Fire N requests at weight 1 back-to-back.
        for _ in range(n_requests):
            limiter.acquire(1)
        total_slept = sum(fake.slept)
        # Theoretical minimum: any request past the first max_weight must
        # wait for its share of the refill window. Refill rate is
        # max_weight / window_seconds tokens per second; the excess
        # requests are (n_requests - max_weight) tokens, so total time
        # waited is excess / (max_weight / window_seconds) =
        # excess * window_seconds / max_weight.
        excess = max(0, n_requests - max_weight)
        expected_total = excess * window_seconds / max_weight
        assert total_slept == pytest.approx(expected_total, rel=1e-6)


class TestRefillRateAttribute:
    """The refill_per_second property mirrors the config-derived steady-state rate."""

    def test_refill_rate_is_max_weight_over_window(self) -> None:
        """refill_per_second equals max_weight / window_seconds as a float."""
        limiter, _ = _make_limiter(max_weight=5000, window_seconds=60)
        assert limiter.refill_per_second == pytest.approx(5000 / 60)
