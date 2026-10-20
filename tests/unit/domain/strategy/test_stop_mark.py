"""Tests for the ``StopRecord`` and ``StopMark`` value objects (#388).

Invariant protected here: both timestamps are timezone-aware, so a stored stop
can always be matched to its position and bar in UTC.

Reference: 388-stop-marks change.md.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.strategy.stop_mark import StopMark, StopRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_ENTRY = datetime(2026, 10, 5, 12, tzinfo=UTC)


def _record(entry_time: datetime = _ENTRY) -> StopRecord:
    return StopRecord(
        entry_time=entry_time,
        stop_price=Decimal("100"),
        chandelier_level=Decimal("100"),
        atr=Decimal("3"),
        high_since_entry=None,
        stop_moved=None,
    )


def test_record_rejects_a_naive_entry_time() -> None:
    """A naive entry time is refused."""
    with pytest.raises(ValueError, match=r"StopRecord\.entry_time"):
        _record(datetime(2026, 10, 5, 12))


def test_mark_rejects_a_naive_bar_close_time() -> None:
    """A naive bar close time is refused."""
    with pytest.raises(ValueError, match=r"StopMark\.bar_close_time"):
        StopMark(
            strategy_id=StrategyId("swing-donchian-4h"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
            bar_close_time=datetime(2026, 10, 5, 16),
            record=_record(),
        )
