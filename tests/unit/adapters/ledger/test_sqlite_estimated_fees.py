"""SqliteLedger marks and lists trades whose fee is an estimate (issue #1014).

Invariants:

- a trade recorded with ``fee_estimated`` is listed by ``list_estimated_fees``
  with its id, pair, side, time and stored fee; an exact-fee trade is not;
- the mark is written in the trade's own transaction: a trade the ledger
  refuses leaves no mark;
- the migration is additive: applied to a ledger from the previous release,
  it adds the table and keeps every trade.
"""

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR, apply_migrations
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.ports.ledger import EstimatedFee, EstimatedFeeSourcePort
from tests.unit.adapters.ledger.test_sqlite_trade_ledger import _BTC_USDT, _T0, _record, _trade

_MIGRATION = "20261018T0100_estimated_fees.sql"


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Yield a migrated in-memory ledger."""
    with closing(sqlite3.connect(":memory:")) as conn:
        yield SqliteLedger(conn)


def test_an_estimated_fee_is_listed_with_its_trade(ledger: SqliteLedger) -> None:
    """The listing names the estimated trade and its stored fee, and skips the exact one."""
    _record(ledger, _trade(OrderSide.BUY, "1", "40000", "40", minutes=0))
    estimated = _record(
        ledger, replace(_trade(OrderSide.BUY, "1", "41000", "41", minutes=5), fee_estimated=True)
    )

    assert ledger.list_estimated_fees() == (
        EstimatedFee(
            trade_id=estimated,
            pair=_BTC_USDT,
            side=OrderSide.BUY,
            executed_at=_T0.replace(minute=5),
            fee=Money(amount=Decimal(41), currency=Currency.USDT),
        ),
    )


def test_estimated_fees_are_listed_oldest_first(ledger: SqliteLedger) -> None:
    """Two estimated fees come back in fill-time order, whatever order they were written in."""
    buy = _trade(OrderSide.BUY, "2", "40000", "40", minutes=0)
    _record(ledger, buy)
    late = _record(
        ledger, replace(_trade(OrderSide.SELL, "1", "42000", "42", minutes=30), fee_estimated=True)
    )
    early = _record(
        ledger, replace(_trade(OrderSide.SELL, "1", "41000", "41", minutes=10), fee_estimated=True)
    )

    assert [fee.trade_id for fee in ledger.list_estimated_fees()] == [early, late]


def test_a_refused_trade_leaves_no_mark(ledger: SqliteLedger) -> None:
    """A SELL beyond the open quantity is refused whole: no trade, no estimated-fee mark."""
    oversold = replace(_trade(OrderSide.SELL, "5", "40000", "40", minutes=0), fee_estimated=True)

    with pytest.raises(LedgerError):
        _record(ledger, oversold)

    assert ledger.list_estimated_fees() == ()


def test_the_ledger_is_an_estimated_fee_source(ledger: SqliteLedger) -> None:
    """SqliteLedger satisfies the port cost basis, P&L and tax read the listing through."""
    assert isinstance(ledger, EstimatedFeeSourcePort) is True


def test_migration_is_additive_and_keeps_existing_trades(tmp_path: Path) -> None:
    """Applied after the previous file, it adds only its table and loses no trade."""
    pre, upto = tmp_path / "pre", tmp_path / "upto"
    pre.mkdir()
    upto.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if path.name < _MIGRATION:
            shutil.copy(path, pre / path.name)
        if path.name <= _MIGRATION:
            shutil.copy(path, upto / path.name)
    with closing(sqlite3.connect(":memory:")) as conn:
        apply_migrations(conn, pre)
        before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.execute(
            "INSERT INTO trades (correlation_id, pair_base, pair_quote, side, qty_amount, "
            "qty_asset, price_amount, fee_amount, fee_currency, executed_at) "
            "VALUES ('c', 'BTC', 'USDT', 'BUY', '1', 'BTC', '40000', '40', 'USDT', "
            "'2026-01-02T10:00:00Z')"
        )
        conn.commit()

        applied = apply_migrations(conn, upto)
        after = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()

    assert (applied, after - before, trades) == ((_MIGRATION,), {"estimated_fees"}, (1,))
