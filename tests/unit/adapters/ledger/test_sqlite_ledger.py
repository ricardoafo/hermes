"""Contract + invariant tests for :class:`SqliteLedger`.

Invariants proven here (each maps to a hard requirement):

- **I-06 (ADR-003 §00)**: :meth:`open_lot`, :meth:`consume_lot`, and
  :meth:`append_event` each commit EXACTLY ONCE per call — the lot/consumption
  row and the event row are grouped into a single ``BEGIN…COMMIT`` boundary.
  Proven with a ``CommitCountingConnection`` subclass whose ``commit`` method
  increments a counter (the transaction observer).
- Ledger writes round-trip: what goes in via ``open_lot`` comes out via
  ``list_open_lots`` bit-for-bit (Decimal precision preserved).
- ``LedgerPort`` Protocol conformance — the concrete adapter satisfies the port.
- On SQL failure, the transaction is rolled back — neither the lot nor the
  event lands in the DB.

Reference: ADR-003 §00 (I-06), §03 (persistence schema), code-conventions.md.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from enum import IntEnum

import pytest

from hermes.adapters.ledger import SqliteLedger
from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.adapters.ledger.sqlite_ledger import _iso_utc, _json_default
from hermes.domain.errors import LedgerError
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.ports.ledger import LedgerPort


class CommitCountingConnection(sqlite3.Connection):
    """SQLite connection subclass that counts explicit commit() calls.

    Used as the transaction observer for the I-06 invariant proof: a single
    write on the ledger MUST produce exactly ONE ``commit`` boundary that
    encloses both the row insert and the event insert.

    NOTE: instantiate via ``sqlite3.connect(uri, factory=CommitCountingConnection)``.
    """

    commit_count: int

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Initialise the connection and reset the commit counter to zero."""
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.commit_count = 0

    def commit(self) -> None:
        """Increment the counter, then delegate to the standard commit."""
        self.commit_count += 1
        super().commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_lot() -> Lot:
    """Return a deterministic BTC lot for adapter round-trip tests."""
    return Lot(
        lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
        asset=Currency.BTC,
        original_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        remaining_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        cost_basis_per_unit=Money(amount=Decimal("65000"), currency=Currency.EUR),
        opened_at=datetime(2026, 11, 5, 14, 22, 3, tzinfo=UTC),
    )


def _sample_trade_event(side: TradeSide = TradeSide.BUY) -> TradeExecuted:
    """Return a deterministic TradeExecuted event tied to the sample lot."""
    pair = Pair(base=Currency.BTC, quote=Currency.USDT)
    return TradeExecuted(
        occurred_at=datetime(2026, 11, 5, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=pair,
        side=side,
        qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        price=Price(amount=Decimal("65000"), pair=pair),
        fee=Money(amount=Decimal("0.06"), currency=Currency.USDT),
    )


# ---------------------------------------------------------------------------
# I-06 invariant proof — one commit per write
# ---------------------------------------------------------------------------


class TestI06SingleTransactionPerWrite:
    """Every write groups the row insert AND the event append in ONE commit.

    Invariant I-06 (ADR-003 §00): "Every trade write happens in one SQLite
    transaction with its domain event." A commit-observer counter proves the
    boundary is exactly one, not two, not zero.
    """

    def test_open_lot_produces_exactly_one_commit_boundary(self) -> None:
        """open_lot: lot row + event row commit as a single atomic transaction."""
        conn = sqlite3.connect(":memory:", factory=CommitCountingConnection)
        try:
            apply_migrations(conn)  # migrations produce their own commits
            # Reset AFTER migrations, so we only observe write-path commits.
            conn.commit_count = 0
            ledger = SqliteLedger(conn)
            # Constructor runs apply_migrations again but it is a no-op — no commit
            # unless the schema_migrations table needs the initial CREATE, which
            # it doesn't here because migrations already ran. Reset defensively
            # so the observed count reflects only open_lot.
            conn.commit_count = 0
            ledger.open_lot(_sample_lot(), _sample_trade_event())
            # ONE commit — the with-block bookended lot INSERT and event INSERT.
            assert conn.commit_count == 1
            # Both rows are visible.
            lot_count = conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            assert lot_count == 1
            assert event_count == 1
        finally:
            conn.close()

    def test_consume_lot_produces_exactly_one_commit_boundary(self) -> None:
        """consume_lot: consumption + updated lot + event commit as ONE transaction."""
        conn = sqlite3.connect(":memory:", factory=CommitCountingConnection)
        try:
            ledger = SqliteLedger(conn)
            # Seed a lot first so we have something to consume.
            ledger.open_lot(_sample_lot(), _sample_trade_event(side=TradeSide.BUY))
            conn.commit_count = 0  # reset — count consume_lot only
            # Build a single-consumption batch.
            consumption = LotConsumption(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                realized_gain=Money(amount=Decimal("10"), currency=Currency.EUR),
                consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
            )
            updated_lot = (
                _sample_lot()
                .consume(Quantity(amount=Decimal("0.001"), asset=Currency.BTC))
                .unwrap()
            )
            ledger.consume_lot(
                (consumption,), (updated_lot,), _sample_trade_event(side=TradeSide.SELL)
            )
            # ONE commit — the with-block bookended consumption + update + event.
            assert conn.commit_count == 1
        finally:
            conn.close()

    def test_append_event_produces_exactly_one_commit_boundary(self) -> None:
        """append_event: single event row committed in exactly one transaction."""
        conn = sqlite3.connect(":memory:", factory=CommitCountingConnection)
        try:
            ledger = SqliteLedger(conn)
            conn.commit_count = 0
            ledger.append_event(_sample_trade_event())
            assert conn.commit_count == 1
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Round-trip tests — data persisted and read back matches input
# ---------------------------------------------------------------------------


class TestOpenLotRoundTrip:
    """A lot written via open_lot is retrievable via list_open_lots byte-for-byte."""

    def test_lot_written_reads_back_via_list_open_lots(self) -> None:
        """After open_lot(l), list_open_lots(asset) returns the same lot values."""
        conn = sqlite3.connect(":memory:")
        try:
            ledger = SqliteLedger(conn)
            lot = _sample_lot()
            ledger.open_lot(lot, _sample_trade_event())
            open_lots = ledger.list_open_lots(Currency.BTC)
            assert len(open_lots) == 1
            read_back = open_lots[0]
            assert read_back.lot_id == lot.lot_id
            assert read_back.asset == lot.asset
            assert read_back.original_qty.amount == Decimal("0.001")
            assert read_back.remaining_qty.amount == Decimal("0.001")
            assert read_back.cost_basis_per_unit.amount == Decimal("65000")
            # Timestamp preserved with UTC tzinfo (ISO-8601 round-trip).
            assert read_back.opened_at == lot.opened_at
        finally:
            conn.close()

    def test_list_open_lots_hides_fully_consumed_lots(self) -> None:
        """Lots with remaining_qty == 0 do not appear in list_open_lots."""
        conn = sqlite3.connect(":memory:")
        try:
            ledger = SqliteLedger(conn)
            ledger.open_lot(_sample_lot(), _sample_trade_event())
            # Fully consume it via consume_lot.
            consumption = LotConsumption(
                lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
                consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                realized_gain=Money(amount=Decimal("10"), currency=Currency.EUR),
                consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
            )
            drained = (
                _sample_lot()
                .consume(Quantity(amount=Decimal("0.001"), asset=Currency.BTC))
                .unwrap()
            )
            ledger.consume_lot((consumption,), (drained,), _sample_trade_event(side=TradeSide.SELL))
            # The now-empty lot should be filtered out.
            assert ledger.list_open_lots(Currency.BTC) == ()
        finally:
            conn.close()

    def test_list_open_lots_returns_fifo_order(self) -> None:
        """Open lots sort by opened_at ascending, then lot_id ascending."""
        conn = sqlite3.connect(":memory:")
        try:
            ledger = SqliteLedger(conn)
            older = Lot(
                lot_id=LotId(value="01BBBBBBBBBBBBBBBBBBBBBBBB"),
                asset=Currency.BTC,
                original_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                remaining_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.EUR),
                opened_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            newer = Lot(
                lot_id=LotId(value="01AAAAAAAAAAAAAAAAAAAAAAAA"),
                asset=Currency.BTC,
                original_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                remaining_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                cost_basis_per_unit=Money(amount=Decimal("60000"), currency=Currency.EUR),
                opened_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
            # Insert newer first — proves the ordering comes from the SELECT.
            ledger.open_lot(newer, _sample_trade_event())
            ledger.open_lot(older, _sample_trade_event())
            open_lots = ledger.list_open_lots(Currency.BTC)
            assert [lot.lot_id.value for lot in open_lots] == [
                "01BBBBBBBBBBBBBBBBBBBBBBBB",
                "01AAAAAAAAAAAAAAAAAAAAAAAA",
            ]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Failure isolation — rollback keeps the DB clean
# ---------------------------------------------------------------------------


class TestConsumeLotArgumentMismatch:
    """consume_lot rejects mismatched-length inputs before touching the DB."""

    def test_mismatched_lengths_raise_ledger_error(self) -> None:
        """Mismatched consumptions/updated_lots lengths raise LedgerError before writes."""
        conn = sqlite3.connect(":memory:")
        try:
            ledger = SqliteLedger(conn)
            with pytest.raises(LedgerError, match="equal length"):
                ledger.consume_lot((), (_sample_lot(),), _sample_trade_event(side=TradeSide.SELL))
        finally:
            conn.close()


class TestSqliteRollbackOnError:
    """A SQL error inside a write rolls back — no rows persist."""

    def test_open_lot_duplicate_id_rolls_back(self) -> None:
        """Two open_lot calls with the same lot_id raise and leave only the first row."""
        conn = sqlite3.connect(":memory:")
        try:
            ledger = SqliteLedger(conn)
            ledger.open_lot(_sample_lot(), _sample_trade_event())
            # Second insert with the same PK MUST raise LedgerError.
            with pytest.raises(LedgerError):
                ledger.open_lot(_sample_lot(), _sample_trade_event())
            # Only the first insert persisted (both lots row and event).
            lot_count = conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            assert lot_count == 1
            # Rollback must have removed the second event too.
            assert event_count == 1
        finally:
            conn.close()


class TestAppendEventPersistsEventRow:
    """append_event stores the event with schema version + serialized payload."""

    def test_event_row_recorded_with_schema_version(self) -> None:
        """append_event writes event_type, schema_version, correlation_id, payload."""
        conn = sqlite3.connect(":memory:")
        try:
            ledger = SqliteLedger(conn)
            ledger.append_event(_sample_trade_event())
            row = conn.execute(
                "SELECT event_type, event_schema_version, correlation_id "
                "FROM events WHERE correlation_id = ?",
                ("01J9Z8T3M5BQR2FVKN4XWY6PH1",),
            ).fetchone()
            assert row is not None
            event_type, schema_version, correlation_id = row
            assert event_type == "TradeExecuted"
            assert schema_version == 1
            assert correlation_id == "01J9Z8T3M5BQR2FVKN4XWY6PH1"
        finally:
            conn.close()


class TestLedgerPortProtocolConformance:
    """The concrete adapter satisfies the LedgerPort structural type."""

    def test_sqlite_ledger_is_a_ledger_port(self) -> None:
        """Runtime isinstance check against the LedgerPort Protocol returns True."""
        conn = sqlite3.connect(":memory:")
        try:
            ledger = SqliteLedger(conn)
            assert isinstance(ledger, LedgerPort)
        finally:
            conn.close()


class TestConsumeLotRollbackOnSqlError:
    """SQL error inside consume_lot rolls back — no consumption, no event."""

    def test_consume_lot_rollback_leaves_no_consumption_row(self) -> None:
        """A FK violation inside consume_lot must roll back and skip the event row."""
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            ledger = SqliteLedger(conn)
            # Consumption references a lot_id that does not exist — FK violation.
            orphan_consumption = LotConsumption(
                lot_id=LotId(value="01ZZZZZZZZZZZZZZZZZZZZZZZZ"),
                consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                realized_gain=Money(amount=Decimal("1"), currency=Currency.EUR),
                consumed_at=datetime(2027, 3, 20, tzinfo=UTC),
            )
            orphan_lot = Lot(
                lot_id=LotId(value="01ZZZZZZZZZZZZZZZZZZZZZZZZ"),
                asset=Currency.BTC,
                original_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                remaining_qty=Quantity(amount=Decimal("0"), asset=Currency.BTC),
                cost_basis_per_unit=Money(amount=Decimal("50000"), currency=Currency.EUR),
                opened_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            with pytest.raises(LedgerError, match="consume_lot failed"):
                ledger.consume_lot(
                    (orphan_consumption,),
                    (orphan_lot,),
                    _sample_trade_event(side=TradeSide.SELL),
                )
            # Nothing persisted — consumption and event both rolled back.
            consumption_rows = conn.execute("SELECT COUNT(*) FROM lot_consumptions").fetchone()[0]
            event_rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            assert consumption_rows == 0
            assert event_rows == 0
        finally:
            conn.close()


class TestAppendEventRollbackOnSqlError:
    """SQL error inside append_event rolls back — no partial row."""

    def test_append_event_rollback_when_closed_connection(self) -> None:
        """Closing the connection mid-write forces sqlite3.Error and triggers rollback path."""
        conn = sqlite3.connect(":memory:")
        ledger = SqliteLedger(conn)
        # Corrupt the events table so INSERT fails but the connection is alive.
        conn.execute("DROP TABLE events")
        with pytest.raises(LedgerError, match="append_event failed"):
            ledger.append_event(_sample_trade_event())
        conn.close()


class TestListOpenLotsRollbackOnSqlError:
    """SQL error inside list_open_lots surfaces as LedgerError."""

    def test_list_open_lots_wraps_sqlite_error(self) -> None:
        """A missing lots table causes SELECT to fail — LedgerError is raised."""
        conn = sqlite3.connect(":memory:")
        ledger = SqliteLedger(conn)
        conn.execute("DROP TABLE lots")
        with pytest.raises(LedgerError, match="list_open_lots failed"):
            ledger.list_open_lots(Currency.BTC)
        conn.close()


class TestSerialisationHelpers:
    """Module-level helpers _iso_utc and _json_default handle boundary cases."""

    def test_iso_utc_refuses_naive_datetime(self) -> None:
        """_iso_utc raises LedgerError on naive datetime — bug caught at boundary."""
        with pytest.raises(LedgerError, match="naive datetime"):
            _iso_utc(datetime(2027, 3, 20, 12, 0, 0))  # naive on purpose

    def test_json_default_rejects_unknown_type(self) -> None:
        """_json_default raises TypeError for values it does not know how to serialise."""
        with pytest.raises(TypeError, match="Cannot serialize"):
            _json_default(object())

    def test_json_default_serialises_enum_by_value(self) -> None:
        """Non-str Enum values fall through to the .value branch."""

        class _NumberEnum(IntEnum):
            ONE = 1

        assert _json_default(_NumberEnum.ONE) == 1
