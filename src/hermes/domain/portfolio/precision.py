"""Decimal precision shared by the portfolio accounting modules.

The default :mod:`decimal` context keeps 28 significant digits and rounds
silently. A product of two 18-digit values needs 36, so portfolio sums could
drift by a rounding step and break the accounting identity
``equity == starting_cash + realized_pnl + unrealized_pnl``. Every portfolio
computation therefore runs in :data:`EXACT_CONTEXT`, wide enough that sums
and products of any realistic fill are exact.

The one inexact operation is the division on a partial SELL. Its quotient is
rounded to :data:`COST_QUANTUM`, the quote-currency precision, so that every
amount in the ledger stays on a fixed grid and later sums remain exact.

Reference: 308-signal-event-portfolio-state change.md D-5.
"""

from __future__ import annotations

from decimal import Context, Decimal
from typing import Final

__all__ = ["COST_QUANTUM", "EXACT_CONTEXT"]

EXACT_CONTEXT: Final[Context] = Context(prec=60)
"""Context for portfolio arithmetic: 60 significant digits."""

COST_QUANTUM: Final[Decimal] = Decimal("1e-8")
"""Grid for the cost released on a partial SELL (eight quote places)."""
