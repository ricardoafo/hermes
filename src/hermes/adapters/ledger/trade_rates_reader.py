"""Read-only lookup of the ECB rate stored on each trade, for the tax pack trail.

Opens the ledger with SQLite's ``mode=ro`` URI and never migrates, like
:class:`~hermes.adapters.ledger.filing_reader.LedgerFilingReader`, so it can
run next to ``hermes serve`` and never changes the ledger.

Reference: openspec/changes/568-tax-pack/change.md R-5.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Self

from hermes.domain.errors import LedgerError
from hermes.domain.filing.trail import TradeRate

__all__ = ["LedgerTradeRates"]


class LedgerTradeRates:
    """The :class:`~hermes.ports.tax_pack.TradeRatesPort` over a SQLite ledger."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an open connection; :meth:`open` makes a read-only one.

        Args:
            connection: The ledger connection.
        """
        self._conn = connection

    @classmethod
    def open(cls, path: Path) -> Self:
        """Open ``path`` read-only.

        Args:
            path: The ledger file.

        Returns:
            A reader over a ``mode=ro`` connection.

        Raises:
            FileNotFoundError: There is no file at ``path``; nothing is created.
            LedgerError: SQLite refused to open the file.
        """
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(str(path))
        try:
            connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"cannot open {path} read-only: {exc}") from exc
        return cls(connection)

    def close(self) -> None:
        """Close the connection."""
        self._conn.close()

    def __enter__(self) -> Self:
        """Return ``self`` for ``with`` blocks."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the connection."""
        self.close()

    def eur_rates(self) -> dict[str, TradeRate]:
        """Return the stored rate per trade id; trades with no rate yet are absent.

        Raises:
            LedgerError: A query failed.
        """
        try:
            rows = self._conn.execute(
                "SELECT id, eur_rate, eur_rate_date FROM trades "
                "WHERE eur_rate IS NOT NULL AND eur_rate_date IS NOT NULL"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"trade rate query failed: {exc}") from exc
        return {
            str(row[0]): TradeRate(rate=Decimal(row[1]), rate_date=date.fromisoformat(row[2]))
            for row in rows
        }
