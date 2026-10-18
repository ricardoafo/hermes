"""The one exact Decimal context for money arithmetic (issue #1180).

The default :mod:`decimal` context keeps 28 significant digits and rounds
silently. A product of two 18-digit values needs 36, and EUR shares from the
FIFO replay carry up to 30 decimals, so portfolio sums and tax figures would
drift by a rounding step. Both run in this 100-digit context, wide enough
that sums and products of any realistic amount are exact; figures are
rounded only where they are booked or shown.

The portfolio used to keep 60 digits and the tax code 100, through two
different mechanisms. One width means one answer to "is this exact?".
"""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Context, localcontext
from typing import Final

__all__ = ["EXACT_CONTEXT", "EXACT_PRECISION", "exact"]

EXACT_PRECISION: Final[int] = 100
"""Significant digits of :data:`EXACT_CONTEXT`."""

EXACT_CONTEXT: Final[Context] = Context(prec=EXACT_PRECISION)
"""Context for exact money arithmetic, for ``EXACT_CONTEXT.add(a, b)`` style calls."""


@contextmanager
def exact() -> Iterator[None]:
    """Run the block in a :data:`EXACT_PRECISION`-digit decimal context."""
    with localcontext() as ctx:
        ctx.prec = EXACT_PRECISION
        yield
