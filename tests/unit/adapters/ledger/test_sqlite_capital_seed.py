"""Tests for SqliteLedger capital seeds and quote legs (change 405-usdt-fifo R-1, R-2, D-3).

Reference: ADR-016 item 4, ADR-003 §00 I-06.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fx.eur_pricing import EurPricing
from hermes.domain.lots.quote_leg import CapitalSeed
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.ledger import PendingSeed, TradeLedgerPort
from tests.unit.adapters.ledger.test_sqlite_ledger import CommitCountingConnection

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_SWING = StrategyId("swing")
_T0 = datetime(2026, 10, 3, 11, 45, tzinfo=UTC)


def _pricing(rate: str) -> EurPricing:
    return EurPricing(
        quote=Currency.USDT, rate=Decimal(rate), rate_date=date(2026, 10, 3), source="ecb:test"
    )


def _seed(amount: str = "1000", at: datetime = _T0) -> CapitalSeed:
    return CapitalSeed(
        strategy_id=_SWING,
        amount=Money(amount=Decimal(amount), currency=Currency.USDT),
        seeded_at=at,
    )


def _seed_event(seed: CapitalSeed) -> ShadowCapitalSeeded:
    return ShadowCapitalSeeded(
        occurred_at=seed.seeded_at,
        correlation_id="seed",
        strategy_id=seed.strategy_id,
        amount=seed.amount,
    )


def _record_seed(ledger: SqliteLedger, seed: CapitalSeed | None = None) -> int:
    chosen = _seed() if seed is None else seed
    return ledger.record_capital_seed(chosen, _seed_event(chosen))


def _trade(
    side: OrderSide,
    qty: str,
    price: str,
    fee: str,
    *,
    minutes: int,
    rate: str | None = "1.25",
    fee_currency: Currency = Currency.USDT,
) -> TradeRecord:
    fee_money = Money(amount=Decimal(fee), currency=fee_currency)
    eur = None
    if rate is not None:
        eur = TradeEur.price(
            _pricing(rate),
            price=Money(amount=Decimal(price), currency=Currency.USDT),
            fee=fee_money,
            base=Currency.BTC,
        )
    return TradeRecord(
        correlation_id=f"c-{minutes}",
        pair=_BTC_USDT,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=fee_money,
        executed_at=_T0 + timedelta(minutes=minutes),
        strategy_id=_SWING,
        lot_id=LotId.new() if side == OrderSide.BUY else None,
        eur=eur,
    )


def _record(ledger: SqliteLedger, trade: TradeRecord) -> int:
    return ledger.record_trade(
        trade,
        TradeExecuted(
            occurred_at=trade.executed_at,
            correlation_id=trade.correlation_id,
            pair=trade.pair,
            side=TradeSide(trade.side.value),
            qty=Quantity(amount=trade.qty, asset=trade.pair.base),
            price=Price(amount=trade.price, pair=trade.pair),
            fee=trade.fee,
            strategy_id=trade.strategy_id,
        ),
    )


def _consumptions(conn: sqlite3.Connection, asset: str) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT consumed_qty, proceeds_eur, cost_eur, fee_eur, realized_gain_eur, trade_id "
        "FROM lot_consumptions WHERE consumed_asset = ? ORDER BY trade_id, id",
        (asset,),
    ).fetchall()


def _gains(conn: sqlite3.Connection, asset: str) -> list[Decimal]:
    return [Decimal(str(row[4])) for row in _consumptions(conn, asset)]


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Yield a fresh in-memory SQLite connection and close it afterwards."""
    with closing(sqlite3.connect(":memory:")) as connection:
        yield connection


@pytest.fixture
def ledger(conn: sqlite3.Connection) -> SqliteLedger:
    """Return a migrated ledger on ``conn``."""
    return SqliteLedger(conn)


class TestRecordCapitalSeed:
    """D-1: the seed is one pending quote lot, written once."""

    def test_seed_opens_one_pending_lot_of_the_full_amount(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant: a 1000 USDT seed is one USDT lot of 1000 with no EUR basis yet."""
        _record_seed(ledger)
        assert conn.execute(
            "SELECT asset, original_qty, remaining_qty, cost_basis_per_unit, strategy_id, "
            "opened_at, trade_id FROM lots"
        ).fetchall() == [("USDT", "1000", "1000", None, "swing", _T0.isoformat(), None)]

    def test_seed_reads_back_pending(self, ledger: SqliteLedger) -> None:
        """Invariant: capital_seed returns the stored seed without a rate."""
        _record_seed(ledger)
        assert ledger.capital_seed(_SWING) == _seed()

    def test_strategy_without_a_seed_reads_none(self, ledger: SqliteLedger) -> None:
        """Invariant: only the seeded strategy has a seed; another strategy reads None."""
        _record_seed(ledger)
        assert [ledger.capital_seed(s) for s in (_SWING, StrategyId("other"))] == [_seed(), None]

    def test_seed_and_its_event_share_one_commit(self) -> None:
        """Invariant (I-06): the lot, the seed row and the event land in one transaction."""
        with closing(sqlite3.connect(":memory:", factory=CommitCountingConnection)) as conn:
            ledger = SqliteLedger(conn)
            assert isinstance(conn, CommitCountingConnection)
            before = conn.commit_count
            _record_seed(ledger)
            assert (conn.commit_count - before, _event_types(conn)) == (
                1,
                ["ShadowCapitalSeeded"],
            )

    def test_second_seed_for_the_strategy_is_refused_and_stores_nothing(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-1.1): one seed per strategy; a second write rolls back whole."""
        _record_seed(ledger)
        with pytest.raises(LedgerError, match="record_capital_seed failed"):
            _record_seed(ledger, _seed(amount="5"))
        assert (
            conn.execute("SELECT COUNT(*) FROM lots").fetchone(),
            _event_types(conn),
        ) == ((1,), ["ShadowCapitalSeeded"])

    def test_priced_seed_is_refused(self, ledger: SqliteLedger) -> None:
        """Invariant (D-1): the seed is always written pending."""
        priced = CapitalSeed(
            strategy_id=_SWING,
            amount=Money(Decimal("1000"), Currency.USDT),
            seeded_at=_T0,
            eur_rate=Decimal("1.25"),
        )
        with pytest.raises(LedgerError, match="pending"):
            ledger.record_capital_seed(priced, _seed_event(priced))


def _event_types(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT event_type FROM events ORDER BY id")]


class TestSeedPricing:
    """The backfill prices a pending seed."""

    def test_pending_seed_is_listed(self, ledger: SqliteLedger) -> None:
        """Invariant: a pending seed appears with its id, asset and date."""
        seed_id = _record_seed(ledger)
        assert ledger.list_pending_seeds() == (
            PendingSeed(seed_id=seed_id, asset=Currency.USDT, seeded_at=_T0),
        )

    def test_priced_seed_is_ok_with_its_value_and_lot_basis(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant: 1000 USDT at 1.25 is worth 800 EUR, 0.8 EUR per unit, no longer pending."""
        seed_id = _record_seed(ledger)
        ledger.apply_seed_eur(seed_id, _pricing("1.25"))
        status, rate, value = conn.execute(
            "SELECT eur_status, eur_rate, value_eur FROM capital_seeds"
        ).fetchone()
        (basis,) = conn.execute("SELECT cost_basis_per_unit FROM lots").fetchone()
        assert (status, Decimal(rate), Decimal(value), Decimal(basis)) == (
            "ok",
            Decimal("1.25"),
            Decimal("800"),
            Decimal("0.8"),
        )

    def test_unknown_seed_is_refused(self, ledger: SqliteLedger) -> None:
        """Invariant: pricing a missing seed raises."""
        with pytest.raises(LedgerError, match="no seed with id 9"):
            ledger.apply_seed_eur(9, _pricing("1.25"))

    def test_pricing_in_another_currency_is_refused(self, ledger: SqliteLedger) -> None:
        """Invariant: a seed is priced only by a rate for the currency it is held in."""
        seed_id = _record_seed(ledger)
        with pytest.raises(LedgerError, match="held in USDT"):
            ledger.apply_seed_eur(seed_id, EurPricing.identity(date(2026, 10, 3)))

    def test_pricing_the_seed_leaves_base_rows_unchanged(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-2.1): re-projecting the quote leg never rewrites BTC consumptions."""
        seed_id = _record_seed(ledger)
        _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=1))
        _record(ledger, _trade(OrderSide.SELL, "0.01", "60000", "0.6", minutes=2))
        before = _consumptions(conn, "BTC")
        ledger.apply_seed_eur(seed_id, _pricing("1.25"))
        assert _consumptions(conn, "BTC") == before != []


class TestQuoteLeg:
    """R-2: with a seed, every trade also moves USDT through FIFO."""

    def test_buy_consumes_the_seed_lot_at_the_trade_rate(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-2.1): BUY 0.01 @ 50000 fee 0.5 disposes 500.5 USDT, valued at 1/rate."""
        seed_id = _record_seed(ledger)
        ledger.apply_seed_eur(seed_id, _pricing("1.25"))
        buy_id = _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=1))
        ((qty, proceeds, cost, fee, gain, trade_id),) = _consumptions(conn, "USDT")
        assert (
            Decimal(str(qty)),
            Decimal(str(proceeds)),
            Decimal(str(cost)),
            Decimal(str(fee)),
            Decimal(str(gain)),
            trade_id,
        ) == (Decimal("500.5"), Decimal("400.4"), Decimal("400.4"), Decimal(0), Decimal(0), buy_id)

    def test_seed_lot_keeps_what_the_buy_did_not_spend(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant: after spending 500.5 of 1000 USDT, the seed lot holds 499.5."""
        _record_seed(ledger)
        _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=1))
        (remaining,) = conn.execute(
            "SELECT remaining_qty FROM lots WHERE asset = 'USDT'"
        ).fetchone()
        assert Decimal(remaining) == Decimal("499.5")

    def test_sell_opens_a_usdt_lot_of_its_net_proceeds(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-2.1): SELL 0.01 @ 60000 fee 0.6 opens a 599.4 USDT lot linked to it."""
        _record_seed(ledger)
        _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=1))
        sell_id = _record(ledger, _trade(OrderSide.SELL, "0.01", "60000", "0.6", minutes=2))
        qty, currency, basis = conn.execute(
            "SELECT original_qty, native_currency, cost_basis_per_unit FROM lots "
            "WHERE trade_id = ?",
            (sell_id,),
        ).fetchone()
        assert (Decimal(qty), currency, Decimal(basis)) == (
            Decimal("599.4"),
            "USDT",
            Decimal("0.8"),
        )

    def test_eur_drift_on_held_usdt_is_a_realized_gain(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (ADR-016 item 4): USDT seeded at 1.25 and spent at 1.00 gains 100 EUR."""
        seed_id = _record_seed(ledger)
        ledger.apply_seed_eur(seed_id, _pricing("1.25"))
        _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0", minutes=1, rate="1"))
        assert _gains(conn, "USDT") == [Decimal("100")]

    def test_base_rows_are_the_same_with_or_without_the_seed(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-2.1): the quote leg does not change BTC lots or consumptions."""
        with closing(sqlite3.connect(":memory:")) as plain_conn:
            plain = SqliteLedger(plain_conn)
            _record_seed(ledger)
            for target in (ledger, plain):
                _record(target, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=1))
                _record(target, _trade(OrderSide.SELL, "0.004", "60000", "0.24", minutes=2))
            assert _btc_state(conn) == _btc_state(plain_conn)

    def test_pending_trade_leaves_its_quote_consumption_unvalued_until_priced(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant: a pending BUY's USDT consumption gains its EUR values on apply_trade_eur."""
        seed_id = _record_seed(ledger)
        ledger.apply_seed_eur(seed_id, _pricing("1.25"))
        trade = _trade(OrderSide.BUY, "0.01", "50000", "0", minutes=1, rate=None)
        buy_id = _record(ledger, trade)
        pending_gain = _consumptions(conn, "USDT")[0][4]
        eur = TradeEur.price(
            _pricing("1"), price=Money(Decimal("50000"), Currency.USDT), fee=trade.fee
        )
        assert eur is not None
        ledger.apply_trade_eur(buy_id, eur)
        assert (pending_gain, _gains(conn, "USDT")) == (None, [Decimal("100")])

    def test_seed_written_after_trades_gives_existing_sells_their_lots(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (D-1): a seed dated before stored trades replays them as its quote leg."""
        _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=1))
        sell_id = _record(ledger, _trade(OrderSide.SELL, "0.01", "60000", "0.6", minutes=2))
        _record_seed(ledger, _seed(at=_T0))
        (qty,) = conn.execute(
            "SELECT original_qty FROM lots WHERE trade_id = ?", (sell_id,)
        ).fetchone()
        assert (
            Decimal(qty),
            [Decimal(str(row[0])) for row in _consumptions(conn, "USDT")],
        ) == (Decimal("599.4"), [Decimal("500.5")])

    def test_fee_in_a_third_asset_is_refused_and_nothing_is_stored(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (Out of scope): with quote tracking on, an EUR fee rolls the trade back."""
        _record_seed(ledger)
        trade = _trade(OrderSide.BUY, "0.01", "50000", "0.4", minutes=1, fee_currency=Currency.EUR)
        with pytest.raises(LedgerError, match="BUY fee in EUR is not supported"):
            _record(ledger, trade)
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)

    def test_buy_beyond_the_seed_is_refused(self, ledger: SqliteLedger) -> None:
        """Invariant: a BUY cannot spend USDT the ledger never held."""
        _record_seed(ledger, _seed(amount="100"))
        with pytest.raises(LedgerError, match="FIFO replay of USDT failed"):
            _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0", minutes=1))


def _btc_state(conn: sqlite3.Connection) -> tuple[object, ...]:
    lots = conn.execute(
        "SELECT original_qty, remaining_qty, cost_basis_per_unit FROM lots "
        "WHERE asset = 'BTC' ORDER BY opened_at"
    ).fetchall()
    consumptions = [row[:5] for row in _consumptions(conn, "BTC")]
    return lots, consumptions


class TestWithoutSeed:
    """D-3 / A-2.2: a ledger without a seed behaves as before."""

    def test_no_usdt_rows_without_a_seed(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-2.2): BUY and SELL without a seed write no USDT lot or consumption."""
        _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=1))
        _record(ledger, _trade(OrderSide.SELL, "0.01", "60000", "0.6", minutes=2))
        assert conn.execute(
            "SELECT (SELECT COUNT(*) FROM lots WHERE asset = 'USDT'), "
            "(SELECT COUNT(*) FROM lot_consumptions WHERE consumed_asset = 'USDT')"
        ).fetchone() == (0, 0)


class TestBaseFee:
    """D-2: a BUY fee charged in BTC reduces the lot, not the EUR cost."""

    def test_buy_with_a_base_fee_opens_the_net_quantity_at_the_gross_eur_cost(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant: BUY 1 BTC @ 100 with 0.01 BTC fee at 1.25 opens 0.99 BTC costing 80 EUR."""
        _record(
            ledger,
            _trade(OrderSide.BUY, "1", "100", "0.01", minutes=1, fee_currency=Currency.BTC),
        )
        qty, basis, native = conn.execute(
            "SELECT original_qty, cost_basis_per_unit, cost_basis_native_per_unit FROM lots"
        ).fetchone()
        with localcontext() as ctx:
            ctx.prec = 100
            eur_basis = (Decimal("80") / Decimal("0.99")).quantize(Decimal("1e-30"))
        assert (Decimal(qty), Decimal(basis), Decimal(native)) == (
            Decimal("0.99"),
            eur_basis,
            Decimal("100") / Decimal("0.99"),
        )


class TestEarliestActivity:
    """D-1: the seed date comes from bars, signals and trades already stored."""

    def test_empty_ledger_has_no_activity(self, ledger: SqliteLedger) -> None:
        """Invariant (A-1.2): no evidence events, no date; one bar gives its time."""
        before = ledger.earliest_activity()
        ledger.append_event(
            BarProcessed(
                occurred_at=_T0, correlation_id="b", pair=_BTC_USDT, interval=KlineInterval.H4
            )
        )
        assert (before, ledger.earliest_activity()) == (None, _T0)

    def test_earliest_evidence_event_wins(self, ledger: SqliteLedger) -> None:
        """Invariant (A-1.1): the oldest BarProcessed or TradeExecuted time is returned."""
        ledger.append_event(
            BarProcessed(
                occurred_at=_T0 + timedelta(hours=4),
                correlation_id="b",
                pair=_BTC_USDT,
                interval=KlineInterval.H4,
            )
        )
        _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=30))
        assert ledger.earliest_activity() == _T0 + timedelta(minutes=30)

    def test_other_events_are_not_evidence(self, ledger: SqliteLedger) -> None:
        """Invariant: a seed event (or any other type) does not date the run."""
        _record_seed(ledger, _seed(at=_T0 - timedelta(days=1)))
        _record(ledger, _trade(OrderSide.BUY, "0.01", "50000", "0.5", minutes=30))
        assert ledger.earliest_activity() == _T0 + timedelta(minutes=30)


def test_adapter_still_satisfies_the_trade_ledger_port(ledger: SqliteLedger) -> None:
    """Invariant: the adapter structurally satisfies TradeLedgerPort with the seed methods."""
    assert isinstance(ledger, TradeLedgerPort)


class TestSqliteFailures:
    """Every seed read and write turns a SQLite failure into a LedgerError."""

    def test_seed_write_failure_is_rolled_back_with_its_lot(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """A failed seed insert leaves no orphan quote lot behind (one transaction)."""
        conn.execute("DROP TABLE capital_seeds")
        with pytest.raises(LedgerError, match="record_capital_seed failed"):
            _record_seed(ledger)
        assert conn.execute("SELECT COUNT(*) FROM lots").fetchone() == (0,)

    @pytest.mark.parametrize(
        ("name", "call"),
        [
            ("capital_seed", lambda ledger: ledger.capital_seed(_SWING)),
            ("list_pending_seeds", lambda ledger: ledger.list_pending_seeds()),
            ("apply_seed_eur", lambda ledger: ledger.apply_seed_eur(1, _pricing("1.25"))),
        ],
    )
    def test_seed_read_failure_is_a_ledger_error(
        self,
        ledger: SqliteLedger,
        conn: sqlite3.Connection,
        name: str,
        call: Callable[[SqliteLedger], object],
    ) -> None:
        """A missing capital_seeds table surfaces as LedgerError naming the call."""
        conn.execute("DROP TABLE capital_seeds")
        with pytest.raises(LedgerError, match=f"{name} failed"):
            call(ledger)

    def test_activity_read_failure_is_a_ledger_error(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """A missing events table surfaces as LedgerError naming earliest_activity."""
        conn.execute("DROP TABLE events")
        with pytest.raises(LedgerError, match="earliest_activity failed"):
            ledger.earliest_activity()
