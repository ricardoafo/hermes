"""The ledger stamps ``recorded_at`` from its injected clock, not the wall clock.

Finding A-15 (issue #1185): the stamps were read from ``datetime.now``
inside the adapter, so a replay or a test could not fix them. One test per
store module that writes a stamp.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.clock.simulated import SimulatedClock
from hermes.adapters.ledger import SqliteLedger
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_FIXED = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Yield an in-memory ledger connection and close it after the test."""
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def _signal() -> SignalEmitted:
    signal = StrategySignal(
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=Decimal("65000"),
        stop_price=Decimal("62000"),
        atr_context=Decimal("1500"),
        signal_time=datetime(2026, 11, 5, 16, 0, 0, tzinfo=UTC),
    )
    return SignalEmitted.from_signal(
        signal,
        strategy_id=StrategyId("swing"),
        pair=_BTC,
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH2",
    )


def _bar() -> Bar:
    open_time = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    return Bar(
        pair=_BTC,
        interval=KlineInterval.H4,
        open_time=open_time,
        close_time=open_time + timedelta(hours=4) - timedelta(milliseconds=1),
        open=Decimal("61000.01"),
        high=Decimal("61500.00"),
        low=Decimal("60900.99"),
        close=Decimal("61234.56"),
        volume=Decimal("123.456789"),
    )


def test_event_row_is_stamped_with_the_injected_clock(conn: sqlite3.Connection) -> None:
    """An appended event's ``recorded_at`` is the clock's instant, as ISO-8601 UTC."""
    SqliteLedger(conn, clock=SimulatedClock(start=_FIXED)).append_event(_signal())

    (stamp,) = conn.execute("SELECT recorded_at FROM events").fetchone()

    assert stamp == "2030-01-02T03:04:05+00:00"


def test_live_bar_row_is_stamped_with_the_injected_clock(conn: sqlite3.Connection) -> None:
    """A recorded live bar's ``recorded_at`` is the clock's instant, as ISO-8601 UTC."""
    SqliteLedger(conn, clock=SimulatedClock(start=_FIXED)).record_live_bar(_bar())

    (stamp,) = conn.execute("SELECT recorded_at FROM live_bars").fetchone()

    assert stamp == "2030-01-02T03:04:05+00:00"


def test_spread_sample_row_is_stamped_with_the_injected_clock(conn: sqlite3.Connection) -> None:
    """A recorded spread sample's ``recorded_at`` is the clock's instant, as ISO-8601 UTC."""
    book_time = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    sample = SpreadSample(
        pair=_BTC,
        sampled_at=book_time - timedelta(milliseconds=150),
        book_time=book_time,
        spread=Decimal("0.00012345"),
        median_spread_1h=Decimal("0.000100005"),
        samples_1h=1,
    )
    SqliteLedger(conn, clock=SimulatedClock(start=_FIXED)).record_spread_sample(sample)

    (stamp,) = conn.execute("SELECT recorded_at FROM spread_samples").fetchone()

    assert stamp == "2030-01-02T03:04:05+00:00"
