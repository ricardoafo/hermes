"""Tests for the BarMissed domain event and the missed-bar line (issue #1078).

Invariants protected here: the event's time is the bar's close time, so the
missed-bar budget counts bars, not restarts; a naive time or a ``handled_at``
before the close is refused; a bar is missed only when serve reaches it more
than the live window after it closed.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.events.bar_missed import BarMissed, reached_late
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_OPEN = datetime(2024, 10, 29, 0, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
_WINDOW = timedelta(minutes=5)


def _bar() -> Bar:
    """Return the 4h bar opening at ``_OPEN``."""
    return Bar(
        pair=_PAIR,
        interval=KlineInterval.H4,
        open_time=_OPEN,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10"),
        volume=Decimal("1"),
        close_time=_OPEN + timedelta(hours=4) - timedelta(milliseconds=1),
    )


def test_event_is_versioned_at_one() -> None:
    """Invariant: the first payload shape is schema version 1."""
    assert BarMissed.__event_schema_version__ == 1


def test_from_bar_dates_the_event_at_the_bar_close() -> None:
    """Invariant: the event time is the bar's close; the wall clock goes in ``handled_at``."""
    bar = _bar()
    handled = bar.close_time + timedelta(hours=5)
    event = BarMissed.from_bar(bar, handled_at=handled, correlation_id=_CID)
    assert (event.occurred_at, event.handled_at, event.pair, event.interval) == (
        bar.close_time,
        handled,
        _PAIR,
        KlineInterval.H4,
    )


def test_a_naive_handled_at_is_refused() -> None:
    """Invariant: both times are timezone-aware."""
    bar = _bar()
    with pytest.raises(ValueError, match="UTC-aware"):
        BarMissed.from_bar(
            bar, handled_at=(bar.close_time + _WINDOW).replace(tzinfo=None), correlation_id=_CID
        )


def test_handled_before_the_close_is_refused() -> None:
    """Invariant: serve cannot reach a bar before it closed."""
    bar = _bar()
    with pytest.raises(ValueError, match="before the bar closed"):
        BarMissed.from_bar(
            bar, handled_at=bar.close_time - timedelta(seconds=1), correlation_id=_CID
        )


def test_a_bar_reached_exactly_at_the_window_edge_is_live() -> None:
    """Invariant: the window is inclusive, as the executor's live-book rule is."""
    bar = _bar()
    assert reached_late(bar, now=bar.close_time + _WINDOW, window=_WINDOW) is False


def test_a_bar_reached_one_microsecond_past_the_window_is_missed() -> None:
    """Invariant: anything past the window is missed."""
    bar = _bar()
    late = bar.close_time + _WINDOW + timedelta(microseconds=1)
    assert reached_late(bar, now=late, window=_WINDOW) is True


@given(after=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=30)))
def test_missed_means_past_the_window(after: timedelta) -> None:
    """Property: a bar is missed exactly when it is reached more than the window after close."""
    bar = _bar()
    assert reached_late(bar, now=bar.close_time + after, window=_WINDOW) is (after > _WINDOW)
