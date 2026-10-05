"""Decisions the parity check compares, and the comparison itself.

A *decision* is what the system did with one bar: no signal, or a signal that
was executed or not. Serve's decisions come from the ledger rows it wrote; the
replay's come from running the same bars through the same strategy and kernel.
Prices are never compared: live books are not the §6.2 synthetic book, so fill
prices legitimately differ (#353 owns price drift).

Pure: no I/O, no clock.

Reference: openspec/changes/358-parity-check/change.md D-1.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "ALERT_SOURCE",
    "Decision",
    "Mismatch",
    "Outcome",
    "ParityReport",
    "compare",
    "parity_alert",
]

ALERT_SOURCE = "parity"
_ALERT_LINES = 3


class Outcome(StrEnum):
    """What happened to a signal."""

    EXECUTED = "executed"
    """A fill was recorded for the signal."""

    NOT_EXECUTED = "not executed"
    """No fill: the order was declined, rejected or never placed."""


@dataclass(frozen=True, slots=True)
class Decision:
    """One signal and what became of it.

    Attributes:
        pair: Pair the signal applies to.
        kind: Entry or exit.
        side: Direction of the signal.
        signal_time: Close time of the bar that triggered the signal, UTC-aware.
        outcome: Executed or not.
        reason: Why it was not executed, when the source knows. Serve does not
            record decline reasons in the ledger, so its decisions carry
            ``None``; the replay carries the kernel's rule.
    """

    pair: Pair
    kind: SignalKind
    side: SignalSide
    signal_time: datetime
    outcome: Outcome
    reason: str | None = None

    @property
    def key(self) -> tuple[Pair, SignalKind, SignalSide, datetime]:
        """The identity two decisions are matched on: pair, kind, side, signal time."""
        return (self.pair, self.kind, self.side, self.signal_time)

    def describe(self) -> str:
        """Return ``entry long executed`` or ``entry long not executed (rule x)``."""
        text = f"{self.kind.value} {self.side.value} {self.outcome.value}"
        return text if self.reason is None else f"{text} ({self.reason})"


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One bar where serve and the replay decided differently.

    Attributes:
        bar_close: Close time of the bar, UTC-aware.
        pair: Pair of the bar.
        serve: What serve did, as text.
        replay: What the replay did, as text.
    """

    bar_close: datetime
    pair: Pair
    serve: str
    replay: str

    def line(self) -> str:
        """Return the one-line report: bar close time, pair and both decisions."""
        return (
            f"MISMATCH {self.bar_close.isoformat()} {self.pair}: "
            f"serve {self.serve}; replay {self.replay}"
        )


@dataclass(frozen=True, slots=True)
class ParityReport:
    """Outcome of comparing one UTC day.

    Attributes:
        day: The UTC day checked.
        matched: Signals both sides produced with the same outcome.
        mismatches: Every difference, oldest bar first.
    """

    day: date
    matched: int
    mismatches: tuple[Mismatch, ...]

    @property
    def in_parity(self) -> bool:
        """``True`` when there is no mismatch."""
        return not self.mismatches


def compare(day: date, serve: Iterable[Decision], replay: Iterable[Decision]) -> ParityReport:
    """Match serve's decisions against the replay's and list the differences.

    Signals are matched on :attr:`Decision.key`. A key on one side only is a
    mismatch; a key on both sides with different outcomes is a mismatch. The
    decline reason is reported but never makes two matching outcomes differ.

    Args:
        day: The UTC day being compared, for the report.
        serve: Decisions recorded by serve.
        replay: Decisions produced by the replay.

    Returns:
        The :class:`ParityReport`.
    """
    served = {d.key: d for d in serve}
    replayed = {d.key: d for d in replay}
    matched = 0
    mismatches: list[Mismatch] = []
    for key in sorted(served.keys() | replayed.keys(), key=_order):
        live, rerun = served.get(key), replayed.get(key)
        if live is not None and rerun is not None and live.outcome is rerun.outcome:
            matched += 1
            continue
        pair, _, _, at = key
        mismatches.append(
            Mismatch(
                bar_close=at,
                pair=pair,
                serve="no signal" if live is None else live.describe(),
                replay="no signal" if rerun is None else rerun.describe(),
            )
        )
    return ParityReport(day=day, matched=matched, mismatches=tuple(mismatches))


def parity_alert(report: ParityReport, *, now: datetime) -> AnomalyDetected | None:
    """Return the alert a mismatching report raises, or ``None`` when in parity.

    The text names bars, pairs and decisions only: it carries no price, equity,
    position size or P&L, because pushed surfaces stay blind to shadow money
    (ruling 1).

    Args:
        report: The finished report.
        now: When the alert is raised, UTC-aware.

    Returns:
        A WARNING :class:`AnomalyDetected` from source ``parity``, or ``None``.
    """
    if report.in_parity:
        return None
    shown = "; ".join(
        f"{m.bar_close.isoformat()} {m.pair}: serve {m.serve}, replay {m.replay}"
        for m in report.mismatches[:_ALERT_LINES]
    )
    more = len(report.mismatches) - _ALERT_LINES
    if more > 0:
        shown += f"; {more} more"
    return AnomalyDetected(
        occurred_at=now,
        correlation_id=f"parity-{report.day.isoformat()}",
        source=ALERT_SOURCE,
        severity=AnomalySeverity.WARNING,
        reason=(
            f"backtest/live parity mismatch on {report.day.isoformat()}: {shown}; "
            f"run `hermes parity check --date {report.day.isoformat()}`"
        ),
    )


def _order(key: tuple[Pair, SignalKind, SignalSide, datetime]) -> tuple[datetime, str, str, str]:
    pair, kind, side, at = key
    return (at, str(pair), kind.value, side.value)
