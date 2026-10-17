"""A ledger write that fails on any error, not only a SQLite one, leaves nothing behind (#853).

SQLite opens a transaction on a write's first statement and keeps it open
after an exception. A write that rolled back only on ``sqlite3.Error`` left
its rows in that open transaction, and the next successful write on the
connection committed them. Each test here fails one write with a non-SQLite
error, makes one good write, and checks the ledger holds only the good one.

The seam with a real serve run is
``tests/integration/scenarios/test_chaos_storage.py::
test_a_non_sqlite_error_mid_write_leaves_nothing_for_the_next_commit``.

Reference: ADR-003 §00 I-06 (one write, one transaction).
"""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, DivisionByZero
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fx.eur_pricing import EurPricing
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.lots.quote_leg import CapitalSeed
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.portfolio.correlation import PairCorrelation
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.position import Position
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.stop_mark import StopMark, StopRecord
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SWING = StrategyId("swing")
_T0 = datetime(2026, 10, 3, 12, tzinfo=UTC)
_H4 = timedelta(hours=4)
_PRICING = EurPricing(
    quote=Currency.USDT, rate=Decimal("1.25"), rate_date=date(2026, 10, 3), source="ecb:test"
)
_LOT_ID = LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1")


class _NotSqliteError(Exception):
    """The injected failure: anything that is not a ``sqlite3.Error``."""


class FailingCommitConnection(sqlite3.Connection):
    """Connection whose next ``commit()`` raises a non-SQLite error once armed.

    The failure lands after every statement of the write has run, so the
    whole write is sitting in the open transaction when it fails.
    """

    fail_next_commit: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Open the connection unarmed."""
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.fail_next_commit = False

    def commit(self) -> None:
        """Raise :class:`_NotSqliteError` once when armed, else commit."""
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise _NotSqliteError("injected after the write's statements")
        super().commit()


@pytest.fixture
def conn() -> Iterator[FailingCommitConnection]:
    """Yield an in-memory connection that can fail its next commit."""
    connection = sqlite3.connect(":memory:", factory=FailingCommitConnection)
    yield connection
    connection.close()


@pytest.fixture
def ledger(conn: FailingCommitConnection) -> SqliteLedger:
    """Return a migrated ledger on ``conn``."""
    return SqliteLedger(conn)


def _contents(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    """Every row of every ledger table, so a stray row anywhere shows up."""
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)  # noqa: S608
        for name in tables
    }


def _good_write(ledger: SqliteLedger) -> None:
    """Make the next successful write, the one that would commit any leftover rows."""
    ledger.append_event(
        BarProcessed(
            occurred_at=_T0,
            correlation_id="next-good-write",
            pair=_ETH,
            interval=KlineInterval.H4,
        )
    )


def _without_the_good_write(
    contents: dict[str, list[tuple[object, ...]]],
) -> dict[str, list[tuple[object, ...]]]:
    return {
        **contents,
        "events": [row for row in contents["events"] if "next-good-write" not in repr(row)],
    }


def _lot(opened_at: datetime = _T0) -> Lot:
    return Lot(
        lot_id=_LOT_ID,
        asset=Currency.BTC,
        original_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        remaining_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        cost_basis_per_unit=Money(amount=Decimal(65000), currency=Currency.EUR),
        strategy_id=_SWING,
        opened_at=opened_at,
    )


def _lot_event(side: TradeSide, occurred_at: datetime = _T0) -> TradeExecuted:
    return TradeExecuted(
        occurred_at=occurred_at,
        correlation_id=f"lot-{side.value}",
        pair=_BTC,
        side=side,
        qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        price=Price(amount=Decimal(65000), pair=_BTC),
        fee=Money(amount=Decimal("0.06"), currency=Currency.USDT),
        strategy_id=_SWING,
    )


def _priced(fee: Money) -> TradeEur:
    eur = TradeEur.price(
        _PRICING,
        price=Money(amount=Decimal(50000), currency=Currency.USDT),
        fee=fee,
        base=Currency.BTC,
    )
    assert eur is not None
    return eur


def _trade(*, priced: bool = True, fee: Money | None = None) -> TradeRecord:
    fee_money = fee if fee is not None else Money(amount=Decimal(25), currency=Currency.USDT)
    eur = _priced(fee_money) if priced else None
    return TradeRecord(
        correlation_id="trade-1",
        pair=_BTC,
        side=OrderSide.BUY,
        qty=Decimal("0.5"),
        price=Decimal(50000),
        fee=fee_money,
        executed_at=_T0,
        strategy_id=_SWING,
        lot_id=LotId.new(),
        eur=eur,
    )


def _trade_event(trade: TradeRecord) -> TradeExecuted:
    return TradeExecuted(
        occurred_at=trade.executed_at,
        correlation_id=trade.correlation_id,
        pair=trade.pair,
        side=TradeSide(trade.side.value),
        qty=Quantity(amount=trade.qty, asset=trade.pair.base),
        price=Price(amount=trade.price, pair=trade.pair),
        fee=trade.fee,
        strategy_id=trade.strategy_id,
    )


def _seed() -> CapitalSeed:
    return CapitalSeed(
        strategy_id=_SWING,
        amount=Money(amount=Decimal(1000), currency=Currency.USDT),
        seeded_at=_T0 - timedelta(hours=1),
    )


def _record_seed(ledger: SqliteLedger) -> int:
    seed = _seed()
    return ledger.record_capital_seed(
        seed,
        ShadowCapitalSeeded(
            occurred_at=seed.seeded_at,
            correlation_id="seed",
            strategy_id=seed.strategy_id,
            amount=seed.amount,
        ),
    )


def _bar() -> Bar:
    return Bar(
        pair=_BTC,
        interval=KlineInterval.H4,
        open_time=_T0,
        close_time=_T0 + _H4 - timedelta(milliseconds=1),
        open=Decimal("61000.01"),
        high=Decimal("61500.00"),
        low=Decimal("60900.99"),
        close=Decimal("61234.56"),
        volume=Decimal("123.456789"),
    )


def _portfolio_mark() -> PortfolioMark:
    position = Position(
        pair=_BTC,
        quantity=Decimal("0.0123"),
        cost_basis=Decimal("740.12345678"),
        mark_price=Decimal("61000.5"),
        marked_at=_T0,
    )
    snapshot = PortfolioSnapshot(
        strategy_id=_SWING,
        quote=Currency.USDT,
        starting_cash=Decimal(5000),
        cash=Decimal("4259.87654322"),
        positions={_BTC: position},
        realized_pnl=Decimal("-1.5"),
        peak_equity=Decimal("5010.25"),
    )
    return PortfolioMark(
        snapshot=snapshot, pair=_BTC, interval=KlineInterval.H4, bar_close_time=_T0
    )


def _stop_mark() -> StopMark:
    return StopMark(
        strategy_id=_SWING,
        pair=_BTC,
        bar_close_time=_T0 + _H4,
        record=StopRecord(
            entry_time=_T0,
            stop_price=Decimal("61000.12"),
            chandelier_level=Decimal("61000.12"),
            atr=Decimal("812.000001"),
            high_since_entry=Decimal("64000.5"),
            stop_moved=True,
        ),
    )


def _spread_sample() -> SpreadSample:
    return SpreadSample(
        pair=_BTC,
        sampled_at=_T0 - timedelta(milliseconds=150),
        book_time=_T0,
        spread=Decimal("0.00012345"),
        median_spread_1h=Decimal("0.000100005"),
        samples_1h=1,
    )


def _correlation() -> PairCorrelation:
    return PairCorrelation(
        pair_a=_BTC,
        pair_b=_ETH,
        window_days=30,
        as_of=_T0,
        correlation=0.8123456789,
        sample_count=29,
    )


def _consume_lot(ledger: SqliteLedger) -> Callable[[], object]:
    ledger.open_lot(_lot(), _lot_event(TradeSide.BUY))
    drained = _lot().consume(Quantity(amount=Decimal("0.001"), asset=Currency.BTC)).unwrap()
    consumption = LotConsumption(
        lot_id=_LOT_ID,
        consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        realized_gain=Money(amount=Decimal(10), currency=Currency.EUR),
        consumed_at=_T0 + _H4,
    )
    return lambda: ledger.consume_lot(
        (consumption,), (drained,), _lot_event(TradeSide.SELL, _T0 + _H4)
    )


def _apply_trade_eur(ledger: SqliteLedger) -> Callable[[], object]:
    pending = _trade(priced=False)
    trade_id = ledger.record_trade(pending, _trade_event(pending))
    eur = _priced(pending.fee)
    return lambda: ledger.apply_trade_eur(trade_id, eur)


def _apply_seed_eur(ledger: SqliteLedger) -> Callable[[], object]:
    seed_id = _record_seed(ledger)
    return lambda: ledger.apply_seed_eur(seed_id, _PRICING)


# Each entry: write method -> (ledger -> the write to fail). Setup writes that
# must already be committed (a lot to consume, a pending trade or seed to
# price) run before the returned callable.
_WRITES: dict[str, Callable[[SqliteLedger], Callable[[], object]]] = {
    "open_lot": lambda lg: lambda: lg.open_lot(_lot(), _lot_event(TradeSide.BUY)),
    "consume_lot": _consume_lot,
    "append_event": lambda lg: lambda: lg.append_event(_lot_event(TradeSide.BUY)),
    "record_live_bar": lambda lg: lambda: lg.record_live_bar(_bar()),
    "record_trade": lambda lg: lambda: lg.record_trade(_trade(), _trade_event(_trade())),
    "apply_trade_eur": _apply_trade_eur,
    "record_capital_seed": lambda lg: lambda: _record_seed(lg),
    "apply_seed_eur": _apply_seed_eur,
    "record_portfolio_mark": lambda lg: lambda: lg.record_portfolio_mark(_portfolio_mark()),
    "record_stop_mark": lambda lg: lambda: lg.record_stop_mark(_stop_mark()),
    "record_spread_sample": lambda lg: lambda: lg.record_spread_sample(_spread_sample()),
    "record_pair_correlation": lambda lg: lambda: lg.record_pair_correlation(_correlation()),
}


def test_the_table_covers_every_public_write_method() -> None:
    """Invariant: a new write method on the ledger cannot skip this file's table.

    A public method that writes is one whose body opens a ``_write`` block.
    """
    writers = {
        name
        for name, member in inspect.getmembers(SqliteLedger, inspect.isfunction)
        if not name.startswith("_") and "self._write(" in inspect.getsource(member)
    }
    assert writers == set(_WRITES)


@pytest.mark.parametrize("method", sorted(_WRITES))
def test_a_non_sqlite_error_in_a_write_leaves_nothing_for_the_next_commit(
    method: str, ledger: SqliteLedger, conn: FailingCommitConnection
) -> None:
    """Invariant (I-06, #853): a write that fails on any error commits none of its rows.

    The error is raised after the write's statements have run; the next good
    write must leave the ledger as it was before the failed one, plus itself.
    """
    failing_write = _WRITES[method](ledger)
    before = _contents(conn)
    conn.fail_next_commit = True
    with pytest.raises(_NotSqliteError):
        failing_write()
    _good_write(ledger)
    assert _without_the_good_write(_contents(conn)) == before


class TestErrorsFromTheWritesOwnData:
    """The reproductions from issue #853: a non-SQLite error raised part way through a write."""

    def test_record_trade_whose_base_fee_is_its_quantity_stores_nothing(
        self, ledger: SqliteLedger, conn: FailingCommitConnection
    ) -> None:
        """Invariant: a BUY whose BTC fee eats its whole quantity leaves no trade row behind.

        The lot projection divides by the net quantity, zero here, after the
        trade row and its lot are inserted.
        """
        trade = _trade(fee=Money(amount=Decimal("0.5"), currency=Currency.BTC))
        with pytest.raises(ArithmeticError):
            ledger.record_trade(trade, _trade_event(trade))
        _good_write(ledger)
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0

    def test_apply_trade_eur_that_fails_in_the_projection_keeps_the_trade_pending(
        self, ledger: SqliteLedger, conn: FailingCommitConnection
    ) -> None:
        """Invariant: pricing that fails after its UPDATE leaves the trade pending.

        Serve's pricing loop logs the failure and goes on, so the next bar's
        write is the commit that would have carried the half-done pricing.
        """
        pending = _trade(priced=False)
        trade_id = ledger.record_trade(pending, _trade_event(pending))
        eur = _priced(pending.fee)
        broken = DivisionByZero("injected in the projection")

        def fail(_: object) -> None:
            raise broken

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(ledger, "_reproject", fail)
            with pytest.raises(DivisionByZero):
                ledger.apply_trade_eur(trade_id, eur)
        _good_write(ledger)
        status = conn.execute("SELECT eur_status FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert status == ("pending",)

    def test_open_lot_with_a_naive_event_time_stores_no_lot(
        self, ledger: SqliteLedger, conn: FailingCommitConnection
    ) -> None:
        """Invariant: an event the ledger refuses after the lot INSERT leaves no lot behind."""
        naive = _lot_event(TradeSide.BUY, occurred_at=_T0.replace(tzinfo=None))
        with pytest.raises(LedgerError, match="naive datetime"):
            ledger.open_lot(_lot(), naive)
        _good_write(ledger)
        assert conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0] == 0


class _FailingBookkeepingConnection(sqlite3.Connection):
    """Connection that raises a non-SQLite error on the migration bookkeeping INSERT."""

    def execute(self, sql: str, parameters: Sequence[object] = (), /) -> sqlite3.Cursor:  # type: ignore[override]
        """Raise on ``INSERT INTO schema_migrations``, else execute."""
        if sql.startswith("INSERT INTO schema_migrations"):
            raise _NotSqliteError("injected between the script and its bookkeeping row")
        return super().execute(sql, parameters)


def test_a_migration_failing_on_a_non_sqlite_error_leaves_nothing_for_the_next_commit(
    tmp_path: Path,
) -> None:
    """Invariant (#853): a migration that fails on any error commits none of its script.

    The script runs inside an explicit ``BEGIN``; the failure lands before the
    bookkeeping row, so the next commit must not carry the script's table.
    """
    (tmp_path / "0001_probe.sql").write_text("CREATE TABLE probe (x INTEGER);\n", encoding="utf-8")
    conn = sqlite3.connect(":memory:", factory=_FailingBookkeepingConnection)
    try:
        with pytest.raises(_NotSqliteError):
            apply_migrations(conn, tmp_path)
        conn.commit()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'probe'"
        ).fetchall()
    finally:
        conn.close()
    assert tables == []
