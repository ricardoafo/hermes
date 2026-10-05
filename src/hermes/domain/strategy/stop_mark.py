"""Stop record and stop mark: the trailing stop of an open position after one bar.

The swing strategy recomputes its chandelier stop on every 4h bar it holds a
position, but a signal only carries the stop of the bar it fires on. The
strategy exposes a :class:`StopRecord` for each such bar, and the serve loop
stores it as a :class:`StopMark` (#388) so the console can show the stop
history and a restart can hand the last stop back to the strategy.

Record-only: nothing here changes which bars signal (pre-registration v1 §11).
These rows hold prices, so AI-facing tools must not read them before
certification (ADR-012 D-02, D-06).

Reference: GitHub issue #388, openspec/changes/388-stop-marks/change.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["StopMark", "StopRecord"]


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class StopRecord:
    """The stop the strategy evaluated on one 4h bar while holding a position.

    Attributes:
        entry_time: ``close_time`` of the position's entry bar (UTC).
        stop_price: The stop in force after this bar.
        chandelier_level: The raw chandelier level, highest high minus the ATR
            multiple, before any rule that keeps the stop from falling.
        atr: The ATR the chandelier level was computed with.
        high_since_entry: Highest high from the entry bar to this bar, or
            ``None`` when the strategy's buffer no longer reaches the entry bar.
        stop_moved: Whether the stop differs from the previous one of this
            position, or ``None`` when no previous stop is known (the entry bar,
            or a restart with no stored stop).
    """

    entry_time: datetime
    stop_price: Decimal
    chandelier_level: Decimal
    atr: Decimal
    high_since_entry: Decimal | None
    stop_moved: bool | None

    def __post_init__(self) -> None:
        """Require a timezone-aware entry time."""
        _require_utc(self.entry_time, "StopRecord.entry_time")


@dataclass(frozen=True, slots=True)
class StopMark:
    """A stop record tied to the strategy, pair and bar it belongs to.

    Attributes:
        strategy_id: The strategy holding the position.
        pair: The position's pair.
        bar_close_time: Close time of the 4h bar the record follows (UTC).
        record: What the strategy evaluated on that bar.
    """

    strategy_id: StrategyId
    pair: Pair
    bar_close_time: datetime
    record: StopRecord

    def __post_init__(self) -> None:
        """Require a timezone-aware bar close time."""
        _require_utc(self.bar_close_time, "StopMark.bar_close_time")
