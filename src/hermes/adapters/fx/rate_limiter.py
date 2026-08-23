"""Token-bucket rate limiter — private helper for the FX adapter package.

This is a deliberate copy of the token-bucket pattern used by the
:mod:`hermes.adapters.exchanges.rate_limiter` module. The FX adapter
must not cross-import from the exchange-adapter package (hex-arch
boundary — adapters own their own infra dependencies). The alternative
— lifting the limiter into a shared ``adapters/_shared/`` module —
adds an import surface that only two callers use today and both size
their bucket differently anyway (Binance: 5000 weight/60s; Frankfurter:
60 req/60s). Duplicating ~120 lines of well-covered code is the smaller
sin than a premature abstraction shared across venues with unrelated
lifecycles.

If a third adapter needs a token bucket, promote to
``adapters/_shared/rate_limiter.py`` and delete both copies in one PR.

Reference: ADR-003 §04 (rate limiting — defensive token bucket per adapter),
docs/decisions/2026-08-23-fx-rate-source-frankfurter.md.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["RateLimitConfig", "RateLimiter"]


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Immutable configuration for a :class:`RateLimiter`.

    Attributes:
        max_weight: Maximum weight tokens available in one full window.
            For Frankfurter this defaults to 60 (a polite req/min cap
            for a free public API with no published limit).
        window_seconds: Length of the refill window in seconds. Together
            with ``max_weight`` this defines the steady-state rate
            (``max_weight / window_seconds`` tokens per second).
    """

    max_weight: int
    window_seconds: int

    def __post_init__(self) -> None:
        """Reject non-positive parameters — either would produce a nonsensical rate."""
        if self.max_weight <= 0:
            raise ValueError(f"RateLimitConfig.max_weight must be > 0, got {self.max_weight}")
        if self.window_seconds <= 0:
            raise ValueError(
                f"RateLimitConfig.window_seconds must be > 0, got {self.window_seconds}"
            )


@dataclass(slots=True)
class RateLimiter:
    """Token-bucket limiter over a weighted request budget.

    Attributes:
        config: The bucket sizing.
        clock: Monotonic clock function returning seconds. Defaults to
            :func:`time.monotonic`; tests inject a fake.
        sleep: Blocking sleep function. Defaults to :func:`time.sleep`;
            tests inject a counter to observe throttling without waiting.
        _tokens: Current available tokens. Refilled lazily on each
            :meth:`acquire` / :meth:`try_acquire` call.
        _last_refill: Monotonic timestamp of the last refill.
    """

    config: RateLimitConfig
    clock: Callable[[], float] = field(default=time.monotonic)
    sleep: Callable[[float], None] = field(default=time.sleep)
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        """Initialise the bucket full and mark the refill origin."""
        self._tokens = float(self.config.max_weight)
        self._last_refill = self.clock()

    @property
    def refill_per_second(self) -> float:
        """Return the steady-state refill rate (tokens per second)."""
        return self.config.max_weight / self.config.window_seconds

    def _refill(self) -> None:
        """Add tokens accumulated since the last refill, capped at ``max_weight``."""
        now = self.clock()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(
            float(self.config.max_weight),
            self._tokens + elapsed * self.refill_per_second,
        )
        self._last_refill = now

    def try_acquire(self, weight: int = 1) -> bool:
        """Attempt to consume ``weight`` tokens without blocking.

        Args:
            weight: Number of tokens to consume. Must be > 0 and <=
                :attr:`RateLimitConfig.max_weight` (otherwise the call
                could never succeed).

        Returns:
            ``True`` if the tokens were consumed, ``False`` if the bucket
            currently has fewer than ``weight`` tokens.

        Raises:
            ValueError: On non-positive ``weight`` or ``weight`` above
                :attr:`RateLimitConfig.max_weight`.
        """
        self._validate_weight(weight)
        self._refill()
        if self._tokens >= weight:
            self._tokens -= weight
            return True
        return False

    def acquire(self, weight: int = 1) -> None:
        """Consume ``weight`` tokens, blocking until they become available.

        Args:
            weight: Number of tokens to consume. See :meth:`try_acquire`
                for the validation contract.

        Raises:
            ValueError: On non-positive ``weight`` or ``weight`` above
                :attr:`RateLimitConfig.max_weight`.
        """
        self._validate_weight(weight)
        self._refill()
        if self._tokens >= weight:
            self._tokens -= weight
            return
        deficit = weight - self._tokens
        wait = deficit / self.refill_per_second
        self.sleep(wait)
        self._tokens = 0.0
        self._last_refill = self.clock()

    def _validate_weight(self, weight: int) -> None:
        """Fail fast on impossible weights so callers see the bug at call time."""
        if weight <= 0:
            raise ValueError(f"RateLimiter weight must be > 0, got {weight}")
        if weight > self.config.max_weight:
            raise ValueError(
                f"RateLimiter weight ({weight}) exceeds max_weight "
                f"({self.config.max_weight}); request would never succeed"
            )
