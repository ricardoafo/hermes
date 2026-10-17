"""The shared status layer: per-stream progress and the one-line verdict.

``hermes status`` prints this report; the console read model, the TUI home
screen and the Telegram ``/status`` reply reuse :func:`build_report`, so every
surface shows the same verdict for the same facts.

Verdict rules, in order:

1. **down**: the systemd unit is not running (failed, stopped, or restarting
   after a crash), or there is no ledger to read.
2. **late**: a stream has not processed a bar by its due time plus grace, or
   has never processed one.
3. **all good**: otherwise.

The report carries no P&L: until certification nothing outcome-related may
reach a surface that could leak into decisions (pre-registration v1).

Reference: openspec/changes/370-hermes-status/change.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from hermes.domain.events.risk_halt_recorded import RiskHaltRecorded
from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.ports.status import ShadowEntry, StatusReadPort

__all__ = [
    "MADRID",
    "StatusReport",
    "StreamState",
    "StreamStatus",
    "UnitState",
    "Verdict",
    "VerdictLevel",
    "build_report",
    "ledger_missing_report",
]

MADRID: Final = ZoneInfo("Europe/Madrid")
_ONE_MS: Final = timedelta(milliseconds=1)
_HALT_LOOKBACK: Final = timedelta(days=30)
"""How far back the last halt is looked up: the SA-9 window of pre-registration §10."""
_DIAGNOSE: Final = "run vps-diagnose from GitHub (gh workflow run vps-diagnose.yml)"


class UnitState(StrEnum):
    """What systemd says about ``hermes.service``."""

    RUNNING = "running"
    RESTARTING = "restarting"
    FAILED = "failed"
    STOPPED = "stopped"
    NOT_INSTALLED = "not installed"
    UNKNOWN = "unknown"


class StreamState(StrEnum):
    """Whether one stream's latest bar arrived in time."""

    ON_TIME = "on time"
    LATE = "late"
    NO_DATA = "no data"


class VerdictLevel(StrEnum):
    """The verdict colour every surface shows first."""

    OK = "ok"
    LATE = "late"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class StreamStatus:
    """Progress of one ``(pair, interval)`` stream.

    Attributes:
        pair: Trading pair.
        interval: Candle-duration bucket.
        last_close: Boundary the newest processed bar closed at, or ``None``.
        next_close: Boundary the next bar closes at, or ``None`` without data.
        due: When the next bar should be processed (close + settle).
        state: On time, late or no data.
        late_by: How far past ``due`` the stream is; zero when on time.
    """

    pair: Pair
    interval: KlineInterval
    last_close: datetime | None
    next_close: datetime | None
    due: datetime | None
    state: StreamState
    late_by: timedelta


@dataclass(frozen=True, slots=True)
class Verdict:
    """The plain-words answer to "is it OK, and do I need to act?".

    Attributes:
        level: ``ok``, ``late`` or ``down``.
        headline: One short sentence, e.g. "All good".
        reason: One sentence on why.
        next_step: "nothing", or the exact next step.
    """

    level: VerdictLevel
    headline: str
    reason: str
    next_step: str


@dataclass(frozen=True, slots=True)
class StatusReport:
    """Everything ``hermes status`` shows, computed at ``generated_at``.

    Attributes:
        generated_at: The instant the report describes, UTC-aware.
        unit: systemd state of ``hermes.service``.
        streams: One entry per configured stream, pair then interval order.
        bars_24h: Bars processed in the last 24 hours, per interval, across pairs.
        signals_24h: Signals recorded in the last 24 hours.
        signals_7d: Signals recorded in the last 7 days.
        open_entries: Open shadow positions, entry facts only.
        verdict: The verdict every surface leads with.
        halts_24h: Halts and kills recorded in the last 24 hours (#749).
        last_halt: The newest halt or kill of the last 30 days, or ``None``.
    """

    generated_at: datetime
    unit: UnitState
    streams: tuple[StreamStatus, ...]
    bars_24h: tuple[tuple[KlineInterval, int], ...]
    signals_24h: int
    signals_7d: int
    open_entries: tuple[ShadowEntry, ...]
    verdict: Verdict
    halts_24h: int = 0
    last_halt: RiskHaltRecorded | None = None


def build_report(
    reader: StatusReadPort,
    *,
    pairs: tuple[Pair, ...],
    intervals: tuple[KlineInterval, ...],
    now: datetime,
    settle: timedelta,
    grace: timedelta,
    unit: UnitState,
) -> StatusReport:
    """Read the ledger through ``reader`` and compute the report at ``now``.

    Args:
        reader: Read-only ledger queries.
        pairs: Configured pairs.
        intervals: Configured intervals.
        now: Reference instant, UTC-aware.
        settle: Delay after a candle boundary before the bar is delivered.
        grace: Extra time past ``due`` before a stream counts as late.
        unit: systemd state of ``hermes.service``.

    Returns:
        The report, verdict included.
    """
    streams = tuple(
        _stream_status(
            pair,
            interval,
            reader.last_processed_close(pair, interval),
            now=now,
            settle=settle,
            grace=grace,
        )
        for pair in pairs
        for interval in intervals
    )
    halts = reader.halts_since(now - _HALT_LOOKBACK)
    day_ago = now - timedelta(hours=24)
    return StatusReport(
        generated_at=now,
        unit=unit,
        streams=streams,
        bars_24h=tuple(
            (interval, reader.count_bars_since(interval, now - timedelta(hours=24)))
            for interval in intervals
        ),
        signals_24h=reader.count_signals_since(now - timedelta(hours=24)),
        signals_7d=reader.count_signals_since(now - timedelta(days=7)),
        open_entries=reader.open_entries(),
        verdict=_verdict(unit, streams, pairs),
        halts_24h=sum(1 for h in halts if h.occurred_at >= day_ago),
        last_halt=halts[0] if halts else None,
    )


def ledger_missing_report(now: datetime, unit: UnitState, path: str) -> StatusReport:
    """Return the report for a host with no ledger file at ``path``.

    Args:
        now: Reference instant, UTC-aware.
        unit: systemd state of ``hermes.service``.
        path: The ledger path that was looked up.

    Returns:
        A ``down`` report with no streams.
    """
    return StatusReport(
        generated_at=now,
        unit=unit,
        streams=(),
        bars_24h=(),
        signals_24h=0,
        signals_7d=0,
        open_entries=(),
        verdict=Verdict(
            level=VerdictLevel.DOWN,
            headline="hermes has no ledger",
            reason=f"There is no ledger at {path}, so hermes has never processed a bar here.",
            next_step=f"check the --db path, or {_DIAGNOSE}",
        ),
    )


def _stream_status(
    pair: Pair,
    interval: KlineInterval,
    last_close: datetime | None,
    *,
    now: datetime,
    settle: timedelta,
    grace: timedelta,
) -> StreamStatus:
    """Classify one stream from its newest processed close."""
    if last_close is None:
        return StreamStatus(pair, interval, None, None, None, StreamState.NO_DATA, timedelta(0))
    # The ledger stores Binance's inclusive close (boundary - 1 ms).
    boundary = last_close + _ONE_MS
    next_close = boundary + interval_duration(interval)
    due = next_close + settle
    overdue = now - due
    if overdue > grace:
        return StreamStatus(pair, interval, boundary, next_close, due, StreamState.LATE, overdue)
    return StreamStatus(
        pair, interval, boundary, next_close, due, StreamState.ON_TIME, timedelta(0)
    )


def _verdict(
    unit: UnitState, streams: tuple[StreamStatus, ...], pairs: tuple[Pair, ...]
) -> Verdict:
    """Apply the down, late, all-good rules in order."""
    if unit is UnitState.FAILED:
        return Verdict(
            VerdictLevel.DOWN,
            "hermes is down",
            "hermes.service crashed, restarted until systemd gave up, and stopped.",
            f"see why with journalctl -u hermes.service, or {_DIAGNOSE}",
        )
    if unit is UnitState.RESTARTING:
        return Verdict(
            VerdictLevel.DOWN,
            "hermes is down",
            "hermes.service crashed and systemd is restarting it.",
            f"see why with journalctl -u hermes.service, or {_DIAGNOSE}",
        )
    if unit is UnitState.STOPPED:
        return Verdict(
            VerdictLevel.DOWN,
            "hermes is down",
            "hermes.service is not running.",
            f"start it with sudo systemctl start hermes.service, or {_DIAGNOSE}",
        )
    missing = [s for s in streams if s.state is StreamState.NO_DATA]
    if missing:
        return Verdict(
            VerdictLevel.LATE,
            "A check has never run",
            f"No bar processed yet for {_names(missing)}.",
            f"nothing if hermes just started; otherwise {_DIAGNOSE}",
        )
    late = [s for s in streams if s.state is StreamState.LATE]
    if late:
        worst = max(late, key=lambda s: s.late_by)
        minutes = int(worst.late_by.total_seconds() // 60)
        when = worst.next_close.astimezone(MADRID).strftime("%H:%M") if worst.next_close else "?"
        return Verdict(
            VerdictLevel.LATE,
            f"The {when} check is {minutes} minutes late",
            f"No {worst.interval.value} bar processed for {_names(late)} since it was due.",
            f"nothing yet if hermes is running; if it stays late, {_DIAGNOSE}",
        )
    coins = sorted({p.base.value for p in pairs})
    return Verdict(
        VerdictLevel.OK,
        "All good",
        f"Watching {_join(coins)}. Every check ran on time.",
        "nothing",
    )


def _names(streams: list[StreamStatus]) -> str:
    """Return ``"BTC/USDT 4h and ETH/USDT 4h"`` style stream names."""
    return _join([f"{s.pair} {s.interval.value}" for s in streams])


def _join(items: list[str]) -> str:
    """Join with commas and a final "and"."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"
