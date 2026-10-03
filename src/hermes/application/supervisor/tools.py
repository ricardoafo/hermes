"""Read-only, outcome-blind supervisor tools (ADR-003 §08, ADR-012 D-06/D-07).

The supervisor (the desk, routines and later the MCP transport) reads the
system only through these tools. Each tool:

- has one read scope (``hermes:read:<name>``); no write or trade scope exists,
  so no tool can place, cancel or change anything;
- returns a typed projection built by code from an allowlist of fields:
  times, enums, pairs and counts. No P&L, equity, price, position value,
  Sharpe, drawdown or win rate reaches the supervisor before certification
  (ADR-012 D-06);
- refuses history before 2026-02-01, the memorization guard of ADR-003 §08:
  a model recalls pre-cutoff market data from training, so its "analysis" of
  that period would be recall.

Reference: issue #470 (part of #34).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from hermes.application.status import UnitState, build_report
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.ports.supervisor import SupervisorReadPort

__all__ = [
    "MEMORIZATION_GUARD",
    "TOOLS",
    "HealthView",
    "MemorizationGuardError",
    "PositionView",
    "SignalView",
    "StreamView",
    "SupervisorContext",
    "SupervisorTool",
    "health",
    "positions",
    "signals",
]

MEMORIZATION_GUARD: Final = datetime(2026, 2, 1, tzinfo=UTC)
"""ADR-003 §08: no tool returns data from before this instant."""


class MemorizationGuardError(ValueError):
    """A request reached back before the memorization guard."""


@dataclass(frozen=True, slots=True)
class SupervisorContext:
    """What every tool needs: the ledger reader and the configured streams."""

    reader: SupervisorReadPort
    pairs: tuple[Pair, ...]
    intervals: tuple[KlineInterval, ...]
    settle: timedelta
    grace: timedelta
    unit: UnitState
    now: datetime


@dataclass(frozen=True, slots=True)
class StreamView:
    """One stream's progress: times and state only."""

    pair: str
    interval: str
    last_close: datetime | None
    next_close: datetime | None
    state: str


@dataclass(frozen=True, slots=True)
class HealthView:
    """The verdict and every stream, as ``hermes status`` computes them."""

    verdict: str
    headline: str
    reason: str
    next_step: str
    streams: tuple[StreamView, ...]
    signals_24h: int
    signals_7d: int


@dataclass(frozen=True, slots=True)
class SignalView:
    """A recorded signal: which pair, entry or exit, and when."""

    pair: str
    kind: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PositionView:
    """An open shadow position: which pair and since when. No price or value."""

    pair: str
    entry_time: datetime


def health(ctx: SupervisorContext) -> HealthView:
    """Return the system's verdict and stream progress."""
    report = build_report(
        ctx.reader,
        pairs=ctx.pairs,
        intervals=ctx.intervals,
        now=ctx.now,
        settle=ctx.settle,
        grace=ctx.grace,
        unit=ctx.unit,
    )
    return HealthView(
        verdict=report.verdict.level.value,
        headline=report.verdict.headline,
        reason=report.verdict.reason,
        next_step=report.verdict.next_step,
        streams=tuple(
            StreamView(
                pair=str(s.pair),
                interval=s.interval.value,
                last_close=s.last_close,
                next_close=s.next_close,
                state=s.state.value,
            )
            for s in report.streams
        ),
        signals_24h=report.signals_24h,
        signals_7d=report.signals_7d,
    )


def signals(ctx: SupervisorContext, *, since: datetime) -> tuple[SignalView, ...]:
    """Return the signals at or after ``since``, oldest first.

    Raises:
        MemorizationGuardError: ``since`` is before 2026-02-01.
    """
    if since < MEMORIZATION_GUARD:
        raise MemorizationGuardError(
            f"since {since.isoformat()} is before the memorization guard "
            f"{MEMORIZATION_GUARD.date().isoformat()} (ADR-003 §08)"
        )
    return tuple(
        SignalView(pair=str(f.pair), kind=f.kind, occurred_at=f.occurred_at)
        for f in ctx.reader.signals_since(since)
    )


def positions(ctx: SupervisorContext) -> tuple[PositionView, ...]:
    """Return the open shadow positions, pair and entry time only."""
    return tuple(
        PositionView(pair=str(e.pair), entry_time=e.entry_time) for e in ctx.reader.open_entries()
    )


@dataclass(frozen=True, slots=True)
class SupervisorTool:
    """A named, scoped read tool."""

    name: str
    scope: str
    summary: str
    run: Callable[..., object]


TOOLS: Final[dict[str, SupervisorTool]] = {
    tool.name: tool
    for tool in (
        SupervisorTool("health", "hermes:read:health", "Verdict and stream progress.", health),
        SupervisorTool(
            "signals", "hermes:read:signals", "Signals since a date, no prices.", signals
        ),
        SupervisorTool(
            "positions", "hermes:read:positions", "Open positions, pair and entry time.", positions
        ),
    )
}
"""Every supervisor tool. All scopes are ``hermes:read:*``; there is no write tool."""
