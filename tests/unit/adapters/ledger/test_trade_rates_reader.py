"""Tests for the read-only trade rate lookup (change 568-tax-pack R-5).

The ledger is written through ``TradeRecorder``, so the lookup is checked
against the rates production stores on each trade.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.trade_rates_reader import LedgerTradeRates
from hermes.domain.errors import LedgerError
from hermes.domain.filing.trail import TradeRate
from tests.unit.adapters.ledger.filing_ledger import RATE, standard_ledger


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Return the standard synthetic ledger file."""
    path = tmp_path / "ledger.sqlite3"
    standard_ledger(path)
    return path


@pytest.fixture
def rates(db: Path) -> Iterator[LedgerTradeRates]:
    """Yield a read-only lookup over the standard ledger."""
    with LedgerTradeRates.open(db) as opened:
        yield opened


def test_every_priced_trade_has_its_stored_rate(rates: LedgerTradeRates) -> None:
    """Invariant: each trade id maps to the rate and date stored on its row."""
    assert rates.eur_rates() == {
        "1": TradeRate(rate=RATE, rate_date=date(2026, 3, 2)),
        "2": TradeRate(rate=RATE, rate_date=date(2026, 4, 1)),
        "3": TradeRate(rate=RATE, rate_date=date(2026, 6, 1)),
    }


def test_a_trade_with_no_rate_yet_is_absent(db: Path) -> None:
    """Invariant: a pending trade is left out, never given a made-up rate."""
    conn = sqlite3.connect(db)
    conn.execute("UPDATE trades SET eur_rate = NULL, eur_rate_date = NULL WHERE id = 2")
    conn.commit()
    conn.close()
    with LedgerTradeRates.open(db) as opened:
        assert "2" not in opened.eur_rates()


def test_the_connection_is_read_only(rates: LedgerTradeRates) -> None:
    """Invariant: the lookup can never change the ledger (mode=ro)."""
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        rates._conn.execute("DELETE FROM trades")


def test_open_refuses_a_missing_file_and_creates_nothing(tmp_path: Path) -> None:
    """Invariant: opening a missing ledger fails without creating a file."""
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        LedgerTradeRates.open(missing)
    assert not missing.exists()


def test_query_failure_is_a_ledger_error(tmp_path: Path) -> None:
    """Invariant: a storage failure surfaces as LedgerError, per the port contract."""
    empty = tmp_path / "empty.sqlite3"
    sqlite3.connect(empty).close()
    with LedgerTradeRates.open(empty) as opened, pytest.raises(LedgerError):
        opened.eur_rates()


def test_rates_are_decimals_not_floats(rates: LedgerTradeRates) -> None:
    """Invariant: rates keep their stored digits."""
    assert all(isinstance(r.rate, Decimal) for r in rates.eur_rates().values())
