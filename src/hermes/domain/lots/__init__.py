"""Domain aggregates for FIFO tax lots and lot consumptions.

Ships the pure-logic building blocks of the tax ledger:

- :class:`Lot` — a BUY-opened tax lot tracking ``remaining_qty`` for FIFO consumption.
- :class:`LotConsumption` — the append-only record of a SELL drawing from a lot.
- :func:`select_lots_fifo` — the pure FIFO selection algorithm (oldest-first,
  deterministic tie-breaker on :class:`LotId` lexical order).

Reference: ADR-003 §03 (persistence schema + FIFO worked example),
ADR-002 (Spanish FIFO tax posture), code-conventions.md §1 (Value Object),
§3 (immutability), §6 (Decimal ledger values).
"""

from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.lots.selection import InsufficientInventory, select_lots_fifo

__all__ = ["InsufficientInventory", "Lot", "LotConsumption", "select_lots_fifo"]
