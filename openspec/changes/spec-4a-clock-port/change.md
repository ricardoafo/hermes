# Change: spec-4a-clock-port

**SDD change**: `spec-4a-clock-port`
**Date**: 2026-08-25
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/spec-4a-clock-port/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Introduces `ClockPort` — the time-abstraction protocol that makes all
domain logic replayable — and its two concrete adapters: `WallClock`
(production) and `SimulatedClock` (testing and backtest). Without this
port, any domain code that calls `datetime.now()` directly is
non-replayable: two executions of the same event stream at different
wall-clock moments produce different decisions, breaking the SA-8
deterministic-replay guarantee. `ClockPort` is the single most
important abstraction for replay determinism (SA-8 §4.1 S-01) and is a
G2-gate prerequisite per success-criteria.md §1.3. This PR delivers
the port protocol and both adapters — wiring existing call sites to
inject `ClockPort` is deferred to a follow-up migration change.

## Scope

### In

- `src/hermes/ports/clock.py` — new `ClockPort` `Protocol` with
  `now() -> datetime` (tz-aware UTC). No `sleep` or `monotonic` methods
  (see D-3).
- `src/hermes/adapters/clock/wall.py` — `WallClock` implementation
  wrapping `now_utc()` from `hermes.infrastructure.time`.
- `src/hermes/adapters/clock/simulated.py` — `SimulatedClock`
  implementation with `now()`, `advance(delta)`, `set(t)`.
- `src/hermes/adapters/clock/__init__.py` — populated with re-exports
  so `from hermes.adapters.clock import WallClock, SimulatedClock` works.
- `tests/unit/ports/test_clock_port.py` — port-conformance unit tests.
- `tests/unit/adapters/clock/test_wall_clock.py` — WallClock unit +
  property tests.
- `tests/unit/adapters/clock/test_simulated_clock.py` — SimulatedClock
  unit + property tests.

### Out

- Wiring existing `datetime.now(UTC)` call sites to inject `ClockPort`
  (call sites enumerated in the report — deferred to a migration PR).
- `MarketDataPort` — that is Spec 4b, a parallel PR.
- Distributed / logical clocks (Lamport, vector) — out of scope for
  hermes MVP1 (SA-8 §5.3 explicit: single-writer model makes `event_seq`
  sufficient).
- Timezone conversions to non-UTC — log-tz is UTC everywhere.
- `sleep(seconds: float)` and `monotonic() -> float` on `ClockPort` —
  no existing call sites for `time.sleep` or `time.monotonic` found in
  `src/` (see D-3).

## Decisions

### D-1: Protocol vs ABC

- **Decision**: `ClockPort` is a `typing.Protocol` (structural typing),
  decorated with `@runtime_checkable`.
- **Alternatives**: (a) `abc.ABC` with `@abstractmethod` — requires
  inheritance, couples adapters to the abstract; (b) duck-typed informal
  contract — no mypy enforcement.
- **Rationale**: Every existing port in `hermes.ports` (`ExchangePort`,
  `KlinesFetcherPort`, `FeeScheduleProviderPort`, etc.) uses
  `typing.Protocol`. Consistency with the established idiom. Structural
  typing means any object with a conforming `now()` method satisfies the
  port without inheriting from it — adapters stay pure and independent.
  `@runtime_checkable` enables `isinstance(obj, ClockPort)` in
  conformance tests.
- **Cost of reversal**: MEDIUM — all adapter imports would need ABC
  inheritance added.

### D-2: Return type of `now()`

- **Decision**: `now() -> datetime`, always timezone-aware UTC.
  Returning a `datetime` is type-safe and readable; `float` (epoch
  seconds) and `int` (ms) lose the tz-awareness guarantee at the type
  level.
- **Alternatives**: (a) `float` (POSIX epoch) — loses tz info;
  (b) `int` (ms since epoch) — same problem; (c) `arrow.Arrow` or
  `pendulum.DateTime` — adds a dependency for no gain.
- **Rationale**: SA-8 §4.1 S-01 cites `datetime` objects directly.
  The existing codebase (`DomainEvent.occurred_at`, all value objects)
  uniformly uses `datetime`. Introducing a different type at the port
  would require conversions at every call site.
- **Cost of reversal**: LOW — swap return annotation and add conversion
  helpers.

### D-3: Include `sleep` / `monotonic` on `ClockPort`?

- **Decision**: No. `ClockPort` exposes only `now() -> datetime`.
- **Alternatives**: Include `sleep(seconds: float)` and
  `monotonic() -> float` — would allow testable delay simulation.
- **Rationale**: Grep of `src/` found zero calls to `time.sleep()` and
  zero calls to `time.monotonic()`. Adding methods with no existing call
  sites violates YAGNI and inflates the protocol surface. Any future
  caller that needs sleep or monotonic can extend the port at that time
  via a separate change.
- **Cost of reversal**: LOW — add methods to the protocol and both
  adapters when a caller needs them.

### D-4: `SimulatedClock` monotonicity policy

- **Decision**: `SimulatedClock.set(t)` raises `ValueError` if
  `t < current`. Time cannot travel backwards.
- **Alternatives**: Silently clamp `t` to current — hides bugs.
- **Rationale**: SA-8 §4.1 S-01 requires deterministic replay. A
  replay that jumps time backwards would produce causally-impossible
  sequences (an event "before" the position was opened, etc.). The
  correct fix when you need to reset time is to create a new
  `SimulatedClock` instance. Raising loudly surfaces any caller that
  tries to travel backwards as a bug, not as a silent wrong result.
- **Cost of reversal**: LOW — remove the guard.

### D-5: Thread safety of `SimulatedClock`

- **Decision**: `SimulatedClock` uses `threading.Lock` to protect its
  internal `_current` state.
- **Alternatives**: No lock — fine for single-threaded use but
  technically unsafe.
- **Rationale**: `grep -r "threading" src/` reveals the existing
  `RateLimiter` already uses `threading.Lock`. Hermes is not
  exclusively single-threaded. Adding a lock has negligible cost (no
  contention in tests; single-threaded replay uses it sequentially) and
  prevents data races if `SimulatedClock` is ever shared across threads
  in calibration or replay orchestration.
- **Cost of reversal**: LOW — remove the lock.

## Requirements

### R-1: `ClockPort` protocol in the ports layer

- **A-1.1**: `python -c "from hermes.ports.clock import ClockPort; print(ClockPort)"` exits 0.
- **A-1.2**: `uv run mypy --strict src/hermes/ports/clock.py` exits 0.

### R-2: `WallClock` satisfies `ClockPort`

- **A-2.1**: `python -c "from hermes.adapters.clock import WallClock; from hermes.ports.clock import ClockPort; assert isinstance(WallClock(), ClockPort)"` exits 0.
- **A-2.2**: `WallClock().now()` returns a `datetime` with non-`None` `tzinfo`.

### R-3: `SimulatedClock` satisfies `ClockPort`

- **A-3.1**: `python -c "from hermes.adapters.clock import SimulatedClock; from hermes.ports.clock import ClockPort; from datetime import datetime, UTC; assert isinstance(SimulatedClock(start=datetime(2025, 1, 1, tzinfo=UTC)), ClockPort)"` exits 0.

### R-4: `SimulatedClock` behaviour

- **A-4.1**: `advance(delta)` moves time forward by exactly `delta`.
- **A-4.2**: `set(t)` where `t >= current` sets the clock to `t`.
- **A-4.3**: `set(t)` where `t < current` raises `ValueError`.
- **A-4.4**: `now()` is non-destructive (repeated calls return same value without advancing).

### R-5: No naive datetimes

- **A-5.1**: `bash scripts/dev/no-naive-datetime.sh` exits 0 after this change.

### R-6: Re-exports work

- **A-6.1**: `python -c "from hermes.adapters.clock import WallClock, SimulatedClock"` exits 0.

### R-7: Full test suite green

- **A-7.1**: `uv run pytest tests/unit/adapters/clock tests/unit/ports/ -x` exits 0.

## Design

### File tree

```text
src/hermes/ports/
  clock.py                        ← new

src/hermes/adapters/clock/
  __init__.py                     ← re-exports (was 0-byte)
  wall.py                         ← new  WallClock
  simulated.py                    ← new  SimulatedClock

tests/unit/
  ports/
    test_clock_port.py            ← new  (create ports/ dir if needed)
  adapters/
    clock/
      __init__.py                 ← new (empty)
      test_wall_clock.py          ← new
      test_simulated_clock.py     ← new
```

### `src/hermes/ports/clock.py`

```python
"""Clock port — abstract Protocol for time sources.

Implementations live in `hermes.adapters.clock`.

- Production: WallClock (wraps hermes.infrastructure.time.now_utc).
- Testing / backtest: SimulatedClock (deterministic, manually advanced).
- Replay: a future ReplayClock (advances with each replayed event).

Reference: SA-8 §4.1 S-01 (deterministic replay requires injected clock).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = ["ClockPort"]


@runtime_checkable
class ClockPort(Protocol):
    """Minimum contract every clock implementation satisfies.

    Any object with a compliant ``now()`` method is a valid ``ClockPort``
    — no inheritance required (structural typing via ``typing.Protocol``).
    ``@runtime_checkable`` enables ``isinstance`` checks in conformance
    tests.
    """

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime.

        Implementations MUST return a ``datetime`` with non-``None``
        ``tzinfo`` (always UTC).  Naive ``datetime`` objects are a
        contract violation.

        Returns:
            A timezone-aware ``datetime`` in UTC.
        """
        ...
```

### `src/hermes/adapters/clock/wall.py`

```python
"""WallClock — production ClockPort using the real wall clock."""

from __future__ import annotations

from datetime import datetime

from hermes.infrastructure.time import now_utc

__all__ = ["WallClock"]


class WallClock:
    """ClockPort implementation backed by the real system clock.

    Delegates to :func:`hermes.infrastructure.time.now_utc` so the
    UTC-awareness guarantee is inherited from the centralised helper.
    Suitable for production use; inject :class:`SimulatedClock` in
    tests and backtest scenarios.
    """

    def now(self) -> datetime:
        """Return the current UTC instant from the system clock.

        Returns:
            A timezone-aware ``datetime`` in UTC.
        """
        return now_utc()
```

### `src/hermes/adapters/clock/simulated.py`

```python
"""SimulatedClock — deterministic ClockPort for tests and backtest."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

__all__ = ["SimulatedClock"]


class SimulatedClock:
    """Deterministic ClockPort for tests, backtest, and replay.

    Time does NOT advance on reads.  Callers must explicitly call
    :meth:`advance` or :meth:`set` to move the clock forward.

    Thread safety: a :class:`threading.Lock` guards ``_current`` so the
    clock is safe to share across threads (e.g. calibration or replay
    orchestration that parallelises work over a shared simulated timeline).

    Args:
        start: Initial simulated time.  MUST be timezone-aware UTC;
            passing a naive ``datetime`` raises ``ValueError``.
    """

    def __init__(self, *, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError(
                f"SimulatedClock.start must be timezone-aware (UTC). Got naive datetime: {start!r}"
            )
        self._current: datetime = start
        self._lock: threading.Lock = threading.Lock()

    def now(self) -> datetime:
        """Return the current simulated time without advancing it.

        Returns:
            A timezone-aware ``datetime`` in UTC.
        """
        with self._lock:
            return self._current

    def advance(self, delta: timedelta) -> None:
        """Move the simulated clock forward by ``delta``.

        Args:
            delta: Time increment.  Must be non-negative; advancing by
                zero is a no-op but is permitted.

        Raises:
            ValueError: If ``delta`` is negative.
        """
        if delta < timedelta(0):
            raise ValueError(f"SimulatedClock.advance: delta must be non-negative, got {delta!r}")
        with self._lock:
            self._current += delta

    def set(self, t: datetime) -> None:
        """Jump the simulated clock to ``t``.

        Args:
            t: New simulated time.  Must be >= the current time (no
                backwards time travel).  Must be timezone-aware UTC.

        Raises:
            ValueError: If ``t < self.now()`` (time travel back) or if
                ``t`` is naive.
        """
        if t.tzinfo is None:
            raise ValueError(f"SimulatedClock.set: t must be timezone-aware, got naive {t!r}")
        with self._lock:
            if t < self._current:
                raise ValueError(
                    f"SimulatedClock.set: cannot travel backwards. "
                    f"current={self._current!r}, requested={t!r}"
                )
            self._current = t
```

### `src/hermes/adapters/clock/__init__.py`

```python
"""Clock adapters — re-export surface.

Usage::

    from hermes.adapters.clock import WallClock, SimulatedClock
"""

from hermes.adapters.clock.simulated import SimulatedClock
from hermes.adapters.clock.wall import WallClock

__all__ = ["SimulatedClock", "WallClock"]
```

## Tasks

- [x] T-1: Create `openspec/changes/spec-4a-clock-port/` directory with
      `change.md` and `issue.md`.
- [x] T-2: Create `src/hermes/ports/clock.py` with `ClockPort` Protocol.
- [x] T-3: Create `src/hermes/adapters/clock/wall.py` with `WallClock`.
- [x] T-4: Create `src/hermes/adapters/clock/simulated.py` with
      `SimulatedClock` (`now`, `advance`, `set`, thread-safe).
- [x] T-5: Populate `src/hermes/adapters/clock/__init__.py` with
      re-exports.
- [x] T-6: Create `tests/unit/adapters/clock/` with `__init__.py`,
      `test_wall_clock.py`, `test_simulated_clock.py`.
- [x] T-7: Create `tests/unit/ports/test_clock_port.py` with
      conformance tests for both adapters.
- [x] T-8: Run `uv run pytest tests/unit/adapters/clock tests/unit/ports/ -x`
      — fix until green.

## Test plan

1. Every acceptance criterion command above passes.
2. `bash scripts/dev/pr-preflight.sh` all green.
3. All CI jobs green on the PR.

## Risks

- **Pre-commit `no-naive-datetime` hook scope**: the hook scans `tests/`
  too. Test files that import `datetime.now` in a string literal comment
  (e.g. docstring examples) could trip the grep. Mitigation: keep all
  `datetime.now` references in tests inside `SimulatedClock` constructor
  arguments using `datetime(..., tzinfo=UTC)` form, never bare
  `datetime.now()`.
- **`typing.Protocol` with `@runtime_checkable` and `mypy --strict`**:
  mypy may emit `error: Explicit return type ... for protocol method`
  if the `...` body is missing a return annotation. Mitigation: all
  protocol methods carry full type annotations.
- **`SimulatedClock` set() race**: if two threads call `set()` with
  `t >= current` concurrently, the last write wins (within the lock).
  This is correct and expected — the lock prevents data races; the
  ordering between two valid `set()` calls is deterministic within each
  lock acquisition.

## Dependencies on other changes

- None. This change introduces a new protocol + adapters; no existing
  code is modified. Wiring call sites is a follow-up migration PR.
