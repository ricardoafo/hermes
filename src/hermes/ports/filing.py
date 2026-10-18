"""FilingLedgerReaderPort — what the tax-filing reports read from the ledger.

Read-only: the filing reports never write the ledger, never re-run the FIFO
projection, and are read by nothing in the strategy, sizing, fees, risk or
kill code (the ``filing-is-measurement-only`` import-linter contract). The
concrete implementation is
:class:`hermes.adapters.ledger.filing_reader.LedgerFilingReader`.

Reference: openspec/archive/36-modelo-100-721/change.md.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from hermes.domain.filing.modelo100 import DisposalPiece, PurchaseRecord
from hermes.domain.filing.modelo721 import UnitTrade
from hermes.domain.value_objects.pair import Pair

__all__ = ["FilingLedgerReaderPort"]


@runtime_checkable
class FilingLedgerReaderPort(Protocol):
    """Read-only ledger queries for Modelo 100 and Modelo 721.

    Adapters MUST raise :class:`hermes.domain.errors.LedgerError` on any
    storage failure.
    """

    def disposal_pieces(self, start: datetime, end: datetime) -> tuple[DisposalPiece, ...]:
        """Return the stored consumptions of every SELL filled in ``[start, end)``."""
        ...

    def purchases(self, start: datetime, end: datetime) -> tuple[PurchaseRecord, ...]:
        """Return every BUY filled in ``[start, end]``."""
        ...

    def unit_trades(self, until: datetime) -> tuple[UnitTrade, ...]:
        """Return every trade filled before ``until``, in fill order."""
        ...

    def last_close(self, pair: Pair, at: datetime) -> Decimal | None:
        """Return the close of the last stored bar of ``pair`` closed at or before ``at``."""
        ...
