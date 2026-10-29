"""Integration tests: ``hermes serve`` records the start capital once (change 405-usdt-fifo R-1).

The real composition root runs over a real SQLite ledger file; the feed and
the clock are fakes. No network is touched.

Reference: ADR-016 item 4, change ``405-usdt-fifo`` D-1.
"""

import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.lots.quote_leg import CapitalSeed
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.serve_loop import BTC, H4_BASE, FakeFeed, build, events

pytestmark = pytest.mark.integration

_NOW = H4_BASE + timedelta(days=30)
_FIRST_BAR = H4_BASE + timedelta(hours=4)


def _start(db: Path) -> StrategyId:
    """Build the composition once, as a serve start does, and release the ledger."""
    composition = build(db, FakeFeed(), _NOW)
    composition.sqlite_connection.close()
    # A process that exits drops its serve lock; this in-process start must do it itself.
    if composition.serve_lock is not None:
        composition.serve_lock.release()
    return composition.strategy_id


def _seed(db: Path, strategy_id: StrategyId) -> CapitalSeed | None:
    connection = sqlite3.connect(db)
    try:
        return SqliteLedger(connection).capital_seed(strategy_id)
    finally:
        connection.close()


def _ledger_with_bars(db: Path) -> None:
    """Store two BarProcessed rows, as a shadow run that predates the seed would."""
    connection = sqlite3.connect(db)
    try:
        ledger = SqliteLedger(connection)
        for hours in (8, 4):
            ledger.append_event(
                BarProcessed(
                    occurred_at=H4_BASE + timedelta(hours=hours),
                    correlation_id=f"bar-{hours}",
                    pair=BTC,
                    interval=KlineInterval.H4,
                )
            )
    finally:
        connection.close()


def test_start_on_an_empty_ledger_seeds_the_capital_budget_now(tmp_path: Path) -> None:
    """Invariant (A-1.2): no activity yet -> one pending seed of capital_budget, dated now."""
    db = tmp_path / "ledger.sqlite3"
    strategy_id = _start(db)
    assert _seed(db, strategy_id) == CapitalSeed(
        strategy_id=strategy_id,
        amount=Money(amount=Decimal("5000"), currency=Currency.USDT),
        seeded_at=_NOW,
    )


def test_start_on_a_running_ledger_dates_the_seed_at_its_first_bar(tmp_path: Path) -> None:
    """Invariant (A-1.1): the seed takes the earliest BarProcessed time, not the restart time."""
    db = tmp_path / "ledger.sqlite3"
    _ledger_with_bars(db)
    strategy_id = _start(db)
    seed = _seed(db, strategy_id)
    assert seed is not None
    assert seed.seeded_at == _FIRST_BAR


def test_second_start_writes_no_second_seed(tmp_path: Path) -> None:
    """Invariant (A-1.1): a restart leaves the one seed and its one event as they were."""
    db = tmp_path / "ledger.sqlite3"
    _ledger_with_bars(db)
    strategy_id = _start(db)
    first = _seed(db, strategy_id)
    _start(db)
    assert (_seed(db, strategy_id), len(events(db, "ShadowCapitalSeeded"))) == (first, 1)
