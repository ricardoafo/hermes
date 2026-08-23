"""Tests for the FX-adapter-local :class:`RateLimiter`.

The FX adapter owns its own copy of the token-bucket limiter (see the
adapter's ``rate_limiter`` module for the DRY-vs-boundary rationale).
This suite pins the semantics: config validation, refill math, blocking
acquire, and non-blocking try_acquire.

Reference: docs/guides/ai-authored-tests.md.
"""

from __future__ import annotations

import pytest

from hermes.adapters.fx.rate_limiter import RateLimitConfig, RateLimiter


class _FakeClock:
    """Deterministic clock that advances by explicit .tick() calls."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        """Advance the clock by ``seconds`` — deterministic for tests."""
        self.now += seconds


class _SleepRecorder:
    """Sleep observer — records duration, does NOT actually sleep."""

    def __init__(self, clock: _FakeClock) -> None:
        self.calls: list[float] = []
        self._clock = clock

    def __call__(self, seconds: float) -> None:
        """Record the sleep and advance the fake clock forward by the same amount."""
        self.calls.append(seconds)
        self._clock.tick(seconds)


class TestConfigValidation:
    """Config invalid values fail at construction, not at first acquire."""

    def test_zero_max_weight_rejected(self) -> None:
        """max_weight=0 raises ValueError — a bucket with no tokens is a spinlock."""
        with pytest.raises(ValueError, match="max_weight"):
            RateLimitConfig(max_weight=0, window_seconds=60)

    def test_zero_window_seconds_rejected(self) -> None:
        """window_seconds=0 raises ValueError — division by zero at refill time."""
        with pytest.raises(ValueError, match="window_seconds"):
            RateLimitConfig(max_weight=60, window_seconds=0)


class TestTryAcquire:
    """try_acquire is non-blocking — returns True if a token exists, False otherwise."""

    def test_try_acquire_succeeds_when_bucket_full(self) -> None:
        """A fresh limiter has max_weight tokens — try_acquire(1) returns True."""
        clock = _FakeClock()
        sleep = _SleepRecorder(clock)
        limiter = RateLimiter(
            config=RateLimitConfig(max_weight=5, window_seconds=60),
            clock=clock,
            sleep=sleep,
        )
        assert limiter.try_acquire(1) is True

    def test_try_acquire_fails_when_bucket_empty(self) -> None:
        """After draining the bucket, try_acquire(1) returns False without blocking."""
        clock = _FakeClock()
        sleep = _SleepRecorder(clock)
        limiter = RateLimiter(
            config=RateLimitConfig(max_weight=2, window_seconds=60),
            clock=clock,
            sleep=sleep,
        )
        limiter.try_acquire(1)
        limiter.try_acquire(1)
        assert limiter.try_acquire(1) is False
        assert sleep.calls == []  # never blocked

    def test_try_acquire_zero_weight_raises(self) -> None:
        """weight=0 raises ValueError — a zero request is a caller bug."""
        clock = _FakeClock()
        sleep = _SleepRecorder(clock)
        limiter = RateLimiter(
            config=RateLimitConfig(max_weight=5, window_seconds=60),
            clock=clock,
            sleep=sleep,
        )
        with pytest.raises(ValueError, match="must be > 0"):
            limiter.try_acquire(0)

    def test_try_acquire_over_max_weight_raises(self) -> None:
        """Weight > max_weight raises — the request could never succeed."""
        clock = _FakeClock()
        sleep = _SleepRecorder(clock)
        limiter = RateLimiter(
            config=RateLimitConfig(max_weight=5, window_seconds=60),
            clock=clock,
            sleep=sleep,
        )
        with pytest.raises(ValueError, match="exceeds max_weight"):
            limiter.try_acquire(6)


class TestAcquire:
    """acquire blocks until the requested tokens are available."""

    def test_acquire_returns_immediately_when_tokens_available(self) -> None:
        """A fresh limiter grants one token instantly — no sleep call."""
        clock = _FakeClock()
        sleep = _SleepRecorder(clock)
        limiter = RateLimiter(
            config=RateLimitConfig(max_weight=5, window_seconds=60),
            clock=clock,
            sleep=sleep,
        )
        limiter.acquire(1)
        assert sleep.calls == []

    def test_acquire_sleeps_for_the_deficit_when_bucket_empty(self) -> None:
        """A drained bucket forces exactly one sleep for the token-earn interval."""
        clock = _FakeClock()
        sleep = _SleepRecorder(clock)
        # 60 tokens / 60 s = 1 token/s → 1 s per token to earn back
        limiter = RateLimiter(
            config=RateLimitConfig(max_weight=60, window_seconds=60),
            clock=clock,
            sleep=sleep,
        )
        # Drain
        for _ in range(60):
            limiter.acquire(1)
        assert sleep.calls == []
        # Next acquire must sleep 1.0s to earn one token
        limiter.acquire(1)
        assert sleep.calls == [1.0]

    def test_refill_property_is_max_over_window(self) -> None:
        """refill_per_second exposes max_weight / window_seconds unchanged."""
        clock = _FakeClock()
        sleep = _SleepRecorder(clock)
        limiter = RateLimiter(
            config=RateLimitConfig(max_weight=60, window_seconds=60),
            clock=clock,
            sleep=sleep,
        )
        assert limiter.refill_per_second == 1.0
