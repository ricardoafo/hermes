"""Tests for the read-only filing reader (change 36-modelo-100-721 A-3.1).

The ledger is written through ``TradeRecorder`` and ``SqliteLedger``, so the
reader is checked against the FIFO projection production stores.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.filing_reader import LedgerFilingReader
from hermes.domain.errors import LedgerError
from hermes.domain.filing.modelo100 import DisposalPiece, PurchaseRecord
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from tests.unit.adapters.ledger.filing_ledger import (
    BTC_USDT,
    BUY1_AT,
    BUY2_AT,
    SELL_AT,
    standard_ledger,
)

_START = datetime(2026, 1, 1, tzinfo=UTC)
_END = datetime(2027, 1, 1, tzinfo=UTC)


@pytest.fixture
def reader(tmp_path: Path) -> Iterator[LedgerFilingReader]:
    """Yield a read-only reader over the standard synthetic ledger."""
    db = tmp_path / "ledger.sqlite3"
    standard_ledger(db)
    with LedgerFilingReader.open(db) as opened:
        yield opened


def test_disposal_pieces_carry_the_stored_fifo_values(reader: LedgerFilingReader) -> None:
    """Invariant: one piece per consumed lot, with the projection's EUR values."""
    pieces = reader.disposal_pieces(_START, _END)
    assert [
        (p.sell_ref, p.buy_ref, p.received, p.qty, p.proceeds_eur, p.cost_eur, p.fee_eur)
        for p in pieces
    ] == [
        ("3", "1", Currency.USDT, Decimal("1"), Decimal("56000"), Decimal("40040"), Decimal("56")),
        (
            "3",
            "2",
            Currency.USDT,
            Decimal("0.5"),
            Decimal("28000"),
            Decimal("24024"),
            Decimal("28"),
        ),
    ]


def test_disposal_pieces_carry_fill_and_lot_times(reader: LedgerFilingReader) -> None:
    """Invariant: disposal time is the SELL's fill time, acquisition the lot's opening."""
    first = reader.disposal_pieces(_START, _END)[0]
    assert (first.disposed_at, first.acquired_at, first.asset) == (SELL_AT, BUY1_AT, Currency.BTC)


def test_disposal_window_is_half_open(reader: LedgerFilingReader) -> None:
    """Invariant: a sale exactly at ``end`` is outside ``[start, end)``."""
    assert reader.disposal_pieces(_START, SELL_AT) == ()


def test_purchases_are_filtered_by_time(reader: LedgerFilingReader) -> None:
    """Invariant: BUYs in the window are returned, oldest id first."""
    assert reader.purchases(BUY2_AT, _END) == (
        PurchaseRecord(ref="2", asset=Currency.BTC, acquired_at=BUY2_AT),
    )


def test_unit_trades_stop_before_until(reader: LedgerFilingReader) -> None:
    """Invariant: only fills strictly before ``until`` are returned, in fill order."""
    trades = reader.unit_trades(SELL_AT)
    assert [(t.ref, t.side, t.qty) for t in trades] == [
        ("1", OrderSide.BUY, Decimal("1")),
        ("2", OrderSide.BUY, Decimal("1")),
    ]


def test_last_close_is_the_latest_bar_closed_by_the_instant(reader: LedgerFilingReader) -> None:
    """Invariant: a bar closing after ``at`` is ignored; the latest earlier close wins."""
    madrid_new_year = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert reader.last_close(BTC_USDT, madrid_new_year) == Decimal("80000")


def test_last_close_without_bars_is_none(reader: LedgerFilingReader) -> None:
    """Invariant: no stored bar before the instant means no price."""
    before_first_bar = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        reader.last_close(BTC_USDT, before_first_bar),
        reader.last_close(BTC_USDT, SELL_AT),
    ) == (
        None,
        None,
    )


def test_open_refuses_a_missing_file(tmp_path: Path) -> None:
    """Invariant: opening a missing ledger creates nothing and raises."""
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(FileNotFoundError):
        LedgerFilingReader.open(missing)
    assert not missing.exists()


def test_reader_cannot_write(tmp_path: Path) -> None:
    """Invariant: the connection is read-only, so a stray write fails."""
    db = tmp_path / "ledger.sqlite3"
    standard_ledger(db)
    with LedgerFilingReader.open(db) as opened, pytest.raises(sqlite3.OperationalError):
        opened._conn.execute("DELETE FROM trades")


def test_a_query_on_a_non_ledger_file_is_a_ledger_error(tmp_path: Path) -> None:
    """Invariant: SQL failures surface as LedgerError, not raw sqlite errors."""
    db = tmp_path / "empty.sqlite3"
    sqlite3.connect(db).close()
    with LedgerFilingReader.open(db) as opened, pytest.raises(LedgerError):
        opened.purchases(_START, _END)


def test_piece_type_is_the_domain_type(reader: LedgerFilingReader) -> None:
    """Invariant: the adapter returns domain values, not rows."""
    assert type(reader.disposal_pieces(_START, _END)[0]) is DisposalPiece
