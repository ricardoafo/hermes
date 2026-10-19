"""Unit tests for the per-bar portfolio marks in the SQLite ledger (#387).

Invariants under test:

- A stored mark reads back as the same snapshot, positions included, exactly.
- One mark per (strategy, bar stream, bar close): a replayed bar writes nothing.
- Range reads are half-open and per strategy; ``latest`` returns the newest.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.position import Position
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.portfolio_history import PortfolioHistoryPort

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_SWING = StrategyId("swing-donchian-4h")
_T0 = datetime(2026, 10, 3, 12, tzinfo=UTC)
_H4 = timedelta(hours=4)


def _snapshot(strategy: StrategyId = _SWING, mark_price: str = "61000.5") -> PortfolioSnapshot:
    position = Position(
        pair=_BTC,
        quantity=Decimal("0.0123"),
        cost_basis=Decimal("740.12345678"),
        mark_price=Decimal(mark_price),
        marked_at=_T0,
    )
    return PortfolioSnapshot(
        strategy_id=strategy,
        quote=Currency.USDT,
        starting_cash=Decimal(5000),
        cash=Decimal("4259.87654322"),
        positions={_BTC: position},
        realized_pnl=Decimal("-1.5"),
        peak_equity=Decimal("5010.25"),
    )


def _mark(i: int, strategy: StrategyId = _SWING) -> PortfolioMark:
    return PortfolioMark(
        snapshot=_snapshot(strategy),
        pair=_BTC,
        interval=KlineInterval.H4,
        bar_close_time=_T0 + _H4 * i - timedelta(milliseconds=1),
    )


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Yield a ledger over an in-memory database."""
    conn = sqlite3.connect(":memory:")
    yield SqliteLedger(conn)
    conn.close()


def test_ledger_satisfies_the_port(ledger: SqliteLedger) -> None:
    """The ledger can be injected wherever PortfolioHistoryPort is expected."""
    assert isinstance(ledger, PortfolioHistoryPort)


def test_mark_round_trips_exactly(ledger: SqliteLedger) -> None:
    """Every snapshot field and position value survives storage without rounding."""
    assert ledger.record_portfolio_mark(_mark(1)) is True
    assert ledger.latest_portfolio_mark(_SWING) == _mark(1)


def test_replayed_bar_writes_nothing(ledger: SqliteLedger) -> None:
    """A restart that re-handles a bar keeps the first mark and reports it."""
    ledger.record_portfolio_mark(_mark(1))
    later = PortfolioMark(
        snapshot=_snapshot(mark_price="1"),
        pair=_BTC,
        interval=KlineInterval.H4,
        bar_close_time=_mark(1).bar_close_time,
    )
    assert ledger.record_portfolio_mark(later) is False
    assert ledger.latest_portfolio_mark(_SWING) == _mark(1)


def test_range_is_half_open_per_strategy_and_ordered(ledger: SqliteLedger) -> None:
    """Reads return [start, end) for one strategy, oldest first."""
    for i in (3, 1, 2, 0):
        ledger.record_portfolio_mark(_mark(i))
    ledger.record_portfolio_mark(_mark(1, StrategyId("other")))
    got = ledger.portfolio_marks(_SWING, _mark(1).bar_close_time, _mark(3).bar_close_time)
    assert got == (_mark(1), _mark(2))


def test_latest_is_per_strategy(ledger: SqliteLedger) -> None:
    """Another strategy's marks never answer for a strategy that has handled no bar."""
    other = StrategyId("other")
    ledger.record_portfolio_mark(_mark(1, other))
    assert (ledger.latest_portfolio_mark(_SWING), ledger.latest_portfolio_mark(other)) == (
        None,
        _mark(1, other),
    )
