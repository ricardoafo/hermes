"""Stateful property test: the SQLite ledger on a real file, across restarts and rollbacks (#844).

How does Hermes fail: trades arrive priced and pending, some back-dated, some
selling more than is open; standalone events land between them; the process
restarts; an operator snapshots the ledger before a migration and later
restores that snapshot. A rule-based state machine drives the real
:class:`SqliteLedger` on a temporary SQLite file through all of it, and a small
reference model (the list of accepted trades plus a time-ordered FIFO replay)
says what the file must hold. Every invariant reads the file itself.

Reference: ADR-003 §00 (I-06), §03 (schema, FIFO), ADR-006 §2 (forward-only,
restore from snapshot), issues #137, #844, #853.
"""

from __future__ import annotations

import copy
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from hermes.adapters.ledger.migration_runner import (
    MIGRATIONS_DIR,
    migrate_with_snapshot,
    restore_snapshot,
)
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fx.eur_pricing import EurPricing
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

_EXPLORE = os.environ.get("HYPOTHESIS_PROFILE") == "explore"
_PAIRS = (
    Pair(base=Currency.BTC, quote=Currency.USDT),
    Pair(base=Currency.ETH, quote=Currency.USDT),
)
_T0 = datetime(2026, 2, 2, 8, 0, tzinfo=UTC)
_STRATEGY = StrategyId("swing")
_PRICING = EurPricing(
    quote=Currency.USDT, rate=Decimal("1.25"), rate_date=date(2026, 2, 2), source="ecb:frankfurter"
)
_QUANTUM = Decimal("0.00000001")
_ZERO = Decimal("0")
_PRICE = Decimal("100")


@dataclass(frozen=True, slots=True)
class _Trade:
    trade_id: int
    base: Currency
    side: OrderSide
    qty: Decimal
    executed_at: datetime


@dataclass(slots=True)
class _Model:
    trades: list[_Trade]
    priced: set[int]
    bar_events: int
    probes: set[str]


def _fifo(trades: list[_Trade], base: Currency) -> dict[int, Decimal] | None:
    """Replay ``base``'s trades in (time, id) order; remaining per BUY id, or None if short."""
    remaining: dict[int, Decimal] = {}
    for trade in sorted(
        (t for t in trades if t.base == base), key=lambda t: (t.executed_at, t.trade_id)
    ):
        if trade.side is OrderSide.BUY:
            remaining[trade.trade_id] = trade.qty
            continue
        left = trade.qty
        for buy_id, open_qty in remaining.items():
            take = min(open_qty, left)
            remaining[buy_id] = open_qty - take
            left -= take
        if left > _ZERO:
            return None
    return remaining


def _event(trade: TradeRecord) -> TradeExecuted:
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


def _record(
    pair: Pair, side: OrderSide, qty: Decimal, at: datetime, *, priced: bool, tag: str
) -> TradeRecord:
    # Excluded transition (issue #853): a fee in the base asset that reaches
    # the quantity raises a non-SQLite error mid-write, and the ledger leaves
    # the half-written rows for the next write to commit. The machine only
    # pays fees in the quote; the pin below reproduces the defect.
    fee = Money(amount=Decimal("0.1"), currency=Currency.USDT)
    eur = (
        TradeEur.price(_PRICING, price=Money(amount=_PRICE, currency=Currency.USDT), fee=fee)
        if priced
        else None
    )
    return TradeRecord(
        correlation_id=tag,
        pair=pair,
        side=side,
        qty=qty,
        price=_PRICE,
        fee=fee,
        executed_at=at,
        strategy_id=_STRATEGY,
        lot_id=LotId.new() if side is OrderSide.BUY else None,
        eur=eur,
    )


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    # No crash is simulated, so skipping fsync only makes the run faster.
    conn.execute("PRAGMA synchronous = OFF")
    return conn


_quantities = st.integers(min_value=1, max_value=300_000_000).map(lambda n: Decimal(n) * _QUANTUM)
# Negative offsets back-date a trade before trades already stored.
_offsets = st.integers(min_value=-180, max_value=240)


class SqliteLedgerMachine(RuleBasedStateMachine):
    """The ledger file holds exactly the accepted trades and their FIFO projection.

    Invariants protected (ADR-003 §00 I-06, §03; ADR-006 §2):

    - nothing lost, nothing doubled: the trade rows are the model's trades, each
      with one ``TradeExecuted`` event and, for a BUY, one lot;
    - trade, event, lot and consumption ids are unique;
    - every lot's remaining quantity is >= 0 and equals the time-ordered FIFO
      replay of the model's trades;
    - each SELL's consumptions sum to its quantity, and each lot's consumptions
      sum to what it lost;
    - a refused write (a SELL beyond the open quantity) leaves the file unchanged;
    - a restart and a migration change nothing; restoring a snapshot brings back
      exactly the state at the snapshot.
    """

    def __init__(self) -> None:
        """Create a fresh ledger file in a temporary directory."""
        super().__init__()
        self.tmp = Path(tempfile.mkdtemp(prefix="hermes-ledger-sm-"))
        self.db = self.tmp / "ledger.sqlite3"
        self.conn = _connect(self.db)
        self.ledger = SqliteLedger(self.conn)
        self.model = _Model(trades=[], priced=set(), bar_events=0, probes=set())
        self.probe_files: set[str] = set()
        self.clock = _T0
        self.tags = 0
        self.snapshots: list[tuple[Path, _Model]] = []
        self.migrations = self.tmp / "migrations"
        shutil.copytree(MIGRATIONS_DIR, self.migrations, ignore=shutil.ignore_patterns("*.py"))

    def teardown(self) -> None:
        """Close the connection and delete the temporary directory."""
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _tag(self) -> str:
        self.tags += 1
        return f"c-{self.tags}"

    def _reopen(self) -> None:
        self.conn = _connect(self.db)
        self.ledger = SqliteLedger(self.conn)

    def _file_state(self) -> tuple[list[tuple[object, ...]], ...]:
        return tuple(
            self.conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
            for table in ("trades", "lots", "lot_consumptions", "events")
        )

    def _open_qty(self, base: Currency) -> Decimal:
        remaining = _fifo(self.model.trades, base)
        assert remaining is not None
        return sum(remaining.values(), _ZERO)

    def _write(self, pair: Pair, side: OrderSide, qty: Decimal, at: datetime, priced: bool) -> None:
        trade = _record(pair, side, qty, at, priced=priced, tag=self._tag())
        probe = _Trade(10**12, pair.base, side, qty, at)
        accepted = _fifo([*self.model.trades, probe], pair.base) is not None
        if not accepted:
            before = self._file_state()
            with pytest.raises(LedgerError, match="FIFO replay"):
                self.ledger.record_trade(trade, _event(trade))
            assert self._file_state() == before
            return
        trade_id = self.ledger.record_trade(trade, _event(trade))
        assert trade_id not in {t.trade_id for t in self.model.trades}
        self.model.trades.append(_Trade(trade_id, pair.base, side, qty, at))
        if priced:
            self.model.priced.add(trade_id)

    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------

    @rule(pair=st.sampled_from(_PAIRS), qty=_quantities, offset=_offsets, priced=st.booleans())
    def buy(self, pair: Pair, qty: Decimal, offset: int, priced: bool) -> None:
        """Record a BUY, possibly back-dated before stored trades."""
        self.clock += timedelta(minutes=max(offset, 0))
        self._write(
            pair, OrderSide.BUY, qty, self.clock + timedelta(minutes=min(offset, 0)), priced
        )

    @rule(
        pair=st.sampled_from(_PAIRS),
        percent=st.integers(min_value=1, max_value=100),
        offset=_offsets,
        priced=st.booleans(),
    )
    def sell(self, pair: Pair, percent: int, offset: int, priced: bool) -> None:
        """Record a SELL of part of what is open; back-dated ones may be refused."""
        qty = (self._open_qty(pair.base) * percent / 100).quantize(_QUANTUM, rounding=ROUND_DOWN)
        if qty == _ZERO:
            return
        self.clock += timedelta(minutes=max(offset, 0))
        self._write(
            pair, OrderSide.SELL, qty, self.clock + timedelta(minutes=min(offset, 0)), priced
        )

    @rule(pair=st.sampled_from(_PAIRS), extra=_quantities)
    def sell_too_much(self, pair: Pair, extra: Decimal) -> None:
        """Record a SELL above the open quantity: refused, nothing written."""
        self._write(pair, OrderSide.SELL, self._open_qty(pair.base) + extra, self.clock, True)

    @rule(pair=st.sampled_from(_PAIRS))
    def bar_event(self, pair: Pair) -> None:
        """Append a standalone ``BarProcessed`` event."""
        self.ledger.append_event(
            BarProcessed(
                occurred_at=self.clock,
                correlation_id=self._tag(),
                pair=pair,
                interval=KlineInterval.H4,
            )
        )
        self.model.bar_events += 1

    @precondition(lambda self: len(self.model.priced) < len(self.model.trades))
    @rule(data=st.data())
    def price_pending(self, data: st.DataObject) -> None:
        """Store the EUR values of one pending trade; the trade set does not change."""
        pending = self.ledger.list_pending_trades()
        expected = sorted(
            (t for t in self.model.trades if t.trade_id not in self.model.priced),
            key=lambda t: (t.executed_at, t.trade_id),
        )
        assert [p.trade_id for p in pending] == [t.trade_id for t in expected]
        target = data.draw(st.sampled_from(expected))
        eur = TradeEur.price(
            _PRICING,
            price=Money(amount=_PRICE, currency=Currency.USDT),
            fee=Money(amount=Decimal("0.1"), currency=Currency.USDT),
        )
        assert eur is not None
        self.ledger.apply_trade_eur(target.trade_id, eur)
        self.model.priced.add(target.trade_id)

    @rule()
    def restart(self) -> None:
        """Close the connection and reopen the file, re-running the startup migrations."""
        self.conn.close()
        self._reopen()

    @precondition(lambda self: len(self.snapshots) < 3)
    @rule()
    def migrate_with_a_snapshot(self) -> None:
        """Snapshot the ledger, then apply a new migration (the operator path, #137)."""
        self.conn.close()
        name = f"99990101T{len(self.probe_files):04d}_probe.sql"
        (self.migrations / name).write_text(
            f"CREATE TABLE IF NOT EXISTS probe_{len(self.probe_files)} (x INTEGER);\n",
            encoding="utf-8",
        )
        self.probe_files.add(name)
        # A distinct time per snapshot: snapshot names have one-second
        # resolution and a second snapshot in the same second overwrites the
        # first (suspected defect, pinned below).
        applied, snapshot = migrate_with_snapshot(
            self.db,
            self.tmp / "snapshots",
            migrations_dir=self.migrations,
            now=_T0 + timedelta(seconds=len(self.probe_files)),
        )
        assert applied == tuple(sorted(self.probe_files - self.model.probes))
        assert snapshot is not None
        self.snapshots.append((snapshot, copy.deepcopy(self.model)))
        self.model.probes = set(self.probe_files)
        self._reopen()

    @precondition(lambda self: bool(self.snapshots))
    @rule(data=st.data())
    def restore_a_snapshot(self, data: st.DataObject) -> None:
        """Roll back to a snapshot: the file holds exactly what it held then."""
        snapshot, model = data.draw(st.sampled_from(self.snapshots))
        self.conn.close()
        restore_snapshot(snapshot, self.db)
        self.model = copy.deepcopy(model)
        self._reopen()

    # ------------------------------------------------------------------
    # invariants
    # ------------------------------------------------------------------

    @invariant()
    def trades_are_exactly_the_accepted_ones(self) -> None:
        """Nothing lost, nothing doubled: trade rows equal the model's trades."""
        rows = self.conn.execute(
            "SELECT id, pair_base, side, qty_amount, executed_at, eur_status "
            "FROM trades ORDER BY id"
        ).fetchall()
        expected = [
            (
                t.trade_id,
                t.base.value,
                t.side.value,
                format(t.qty, "f"),
                t.executed_at.isoformat(),
                "ok" if t.trade_id in self.model.priced else "pending",
            )
            for t in sorted(self.model.trades, key=lambda t: t.trade_id)
        ]
        assert rows == expected

    @invariant()
    def applied_migrations_match_the_model(self) -> None:
        """Every shipped migration is applied; probe migrations follow snapshots and restores."""
        applied = {row[0] for row in self.conn.execute("SELECT filename FROM schema_migrations")}
        shipped = {p.name for p in MIGRATIONS_DIR.glob("*.sql")}
        assert applied == shipped | self.model.probes

    @invariant()
    def one_event_per_trade_and_per_bar(self) -> None:
        """Each trade has one TradeExecuted event; each bar append one BarProcessed."""
        counts = dict(
            self.conn.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type")
        )
        assert counts.get("TradeExecuted", 0) == len(self.model.trades)
        assert counts.get("BarProcessed", 0) == self.model.bar_events
        correlations = [
            row[0]
            for row in self.conn.execute(
                "SELECT correlation_id FROM events WHERE event_type = 'TradeExecuted'"
            )
        ]
        assert len(correlations) == len(set(correlations))

    @invariant()
    def lots_follow_the_time_ordered_fifo(self) -> None:
        """One lot per BUY, ids unique, remaining >= 0 and equal to the FIFO replay."""
        rows = self.conn.execute("SELECT lot_id, trade_id, remaining_qty FROM lots").fetchall()
        assert len({row[0] for row in rows}) == len(rows)
        remaining = {int(row[1]): Decimal(row[2]) for row in rows}
        expected: dict[int, Decimal] = {}
        for pair in _PAIRS:
            replay = _fifo(self.model.trades, pair.base)
            assert replay is not None
            expected |= replay
        assert remaining == expected
        assert all(qty >= _ZERO for qty in remaining.values())

    @invariant()
    def consumptions_sum_to_sells_and_to_lot_draws(self) -> None:
        """Each SELL's consumptions sum to its quantity; each lot's to what it lost."""
        per_sell: dict[int, Decimal] = {}
        per_lot: dict[str, Decimal] = {}
        for lot_id, qty, trade_id in self.conn.execute(
            "SELECT lot_id, consumed_qty, trade_id FROM lot_consumptions"
        ):
            per_sell[int(trade_id)] = per_sell.get(int(trade_id), _ZERO) + Decimal(qty)
            per_lot[lot_id] = per_lot.get(lot_id, _ZERO) + Decimal(qty)
        sells = {t.trade_id: t.qty for t in self.model.trades if t.side is OrderSide.SELL}
        assert per_sell == sells
        drawn = {
            lot_id: Decimal(original) - Decimal(left)
            for lot_id, original, left in self.conn.execute(
                "SELECT lot_id, original_qty, remaining_qty FROM lots"
            )
            if Decimal(original) != Decimal(left)
        }
        assert per_lot == drawn


SqliteLedgerMachine.TestCase.settings = settings(
    max_examples=100 if _EXPLORE else 25,
    stateful_step_count=40 if _EXPLORE else 20,
)
TestSqliteLedgerMachine = SqliteLedgerMachine.TestCase


@pytest.mark.xfail(
    strict=True,
    reason=(
        "issue #853: SqliteLedger rolls back only on sqlite3.Error; a non-SQLite "
        "exception mid-write leaves rows the next write commits"
    ),
)
def test_a_write_that_raises_mid_transaction_leaves_nothing_behind(tmp_path: Path) -> None:
    """A write that raises commits nothing, not even through the next write (I-06).

    Shrunk from the machine's excluded transition: a BUY whose base-asset fee
    equals its quantity raises ``DivisionByZero`` in the lot projection; the
    next good BUY then commits the first trade row too.
    """
    conn = _connect(tmp_path / "ledger.sqlite3")
    ledger = SqliteLedger(conn)
    pair = _PAIRS[0]
    broken = TradeRecord(
        correlation_id="broken",
        pair=pair,
        side=OrderSide.BUY,
        qty=Decimal("1"),
        price=_PRICE,
        fee=Money(amount=Decimal("1"), currency=Currency.BTC),
        executed_at=_T0,
        strategy_id=_STRATEGY,
        lot_id=LotId.new(),
        eur=None,
    )
    with pytest.raises(ArithmeticError):
        ledger.record_trade(broken, _event(broken))
    good = _record(pair, OrderSide.BUY, Decimal("1"), _T0, priced=False, tag="good")
    ledger.record_trade(good, _event(good))
    rows = conn.execute("SELECT correlation_id FROM trades ORDER BY id").fetchall()
    assert rows == [("good",)]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "suspected: migrate_with_snapshot names snapshots to the second and overwrites an "
        "existing file, so a second snapshot in the same second destroys the first rollback point"
    ),
)
def test_a_second_snapshot_never_overwrites_the_first(tmp_path: Path) -> None:
    """Every snapshot taken stays restorable (ADR-006 §2: recovery is restoring a backup).

    Shrunk from the machine: snapshot before probe migration A, restore it,
    record a trade, snapshot again before A in the same second. The first
    snapshot file now holds the trade, so restoring it no longer rolls back.
    """
    migrations = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, migrations, ignore=shutil.ignore_patterns("*.py"))
    (migrations / "99990101T0000_probe.sql").write_text(
        "CREATE TABLE IF NOT EXISTS probe_0 (x INTEGER);\n", encoding="utf-8"
    )
    db = tmp_path / "ledger.sqlite3"
    conn = _connect(db)
    SqliteLedger(conn)
    conn.close()
    _, first = migrate_with_snapshot(db, tmp_path / "snapshots", migrations_dir=migrations, now=_T0)
    assert first is not None
    restore_snapshot(first, db)
    conn = _connect(db)
    trade = _record(_PAIRS[0], OrderSide.BUY, Decimal("1"), _T0, priced=False, tag="after")
    SqliteLedger(conn).record_trade(trade, _event(trade))
    conn.close()
    migrate_with_snapshot(db, tmp_path / "snapshots", migrations_dir=migrations, now=_T0)
    restore_snapshot(first, db)
    conn = _connect(db)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
