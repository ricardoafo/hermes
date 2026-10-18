"""Read-only lookup of the ECB rate stored on each trade, for the tax pack trail.

Opens the ledger with SQLite's ``mode=ro`` URI and never migrates, like
:class:`~hermes.adapters.ledger.filing_reader.LedgerFilingReader`, so it can
run next to ``hermes serve`` and never changes the ledger.

Reference: openspec/changes/568-tax-pack/change.md R-5.
"""

from __future__ import annotations

import sqlite3

from hermes.adapters.ledger._cells import date_cell, decimal_cell
from hermes.adapters.ledger.connection import ReadOnlyLedgerReader
from hermes.domain.errors import LedgerError
from hermes.domain.filing.trail import TradeRate

__all__ = ["LedgerTradeRates"]


class LedgerTradeRates(ReadOnlyLedgerReader):
    """The :class:`~hermes.ports.tax_pack.TradeRatesPort` over a SQLite ledger."""

    def eur_rates(self) -> dict[str, TradeRate]:
        """Return the stored rate per trade id; trades with no rate yet are absent.

        Raises:
            LedgerError: A query failed or a row holds an unreadable rate or date.
        """
        try:
            rows = self._conn.execute(
                "SELECT id, eur_rate, eur_rate_date FROM trades "
                "WHERE eur_rate IS NOT NULL AND eur_rate_date IS NOT NULL"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"trade rate query failed: {exc}") from exc
        try:
            return {
                str(row[0]): TradeRate(rate=decimal_cell(row[1]), rate_date=date_cell(row[2]))
                for row in rows
            }
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"unreadable trade rate row: {exc}") from exc
