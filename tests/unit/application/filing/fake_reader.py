"""Scripted FilingLedgerReaderPort for the service tests.

The seam it replaces is covered against a real ledger file in
``tests/unit/adapters/ledger/test_filing_reader.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from hermes.domain.filing.modelo100 import DisposalPiece, PurchaseRecord
from hermes.domain.filing.modelo721 import UnitTrade
from hermes.domain.value_objects.pair import Pair


@dataclass
class FakeFilingReader:
    """Answers from fixed lists; filters by time like the adapter does."""

    pieces: list[DisposalPiece] = field(default_factory=list)
    buys: list[PurchaseRecord] = field(default_factory=list)
    trades: list[UnitTrade] = field(default_factory=list)
    closes: dict[Pair, Decimal] = field(default_factory=dict)
    close_queries: list[tuple[Pair, datetime]] = field(default_factory=list)
    purchase_queries: list[tuple[datetime, datetime]] = field(default_factory=list)

    def disposal_pieces(self, start: datetime, end: datetime) -> tuple[DisposalPiece, ...]:
        """Return the scripted pieces disposed in ``[start, end)``."""
        return tuple(p for p in self.pieces if start <= p.disposed_at < end)

    def purchases(self, start: datetime, end: datetime) -> tuple[PurchaseRecord, ...]:
        """Return the scripted buys in ``[start, end]`` and record the range."""
        self.purchase_queries.append((start, end))
        return tuple(b for b in self.buys if start <= b.acquired_at <= end)

    def unit_trades(self, until: datetime) -> tuple[UnitTrade, ...]:
        """Return the scripted trades before ``until``."""
        return tuple(t for t in self.trades if t.executed_at < until)

    def last_close(self, pair: Pair, at: datetime) -> Decimal | None:
        """Return the scripted close of ``pair`` and record the query."""
        self.close_queries.append((pair, at))
        return self.closes.get(pair)
