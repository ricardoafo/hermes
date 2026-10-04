"""Decimal context for tax arithmetic.

EUR shares from the FIFO replay carry up to 30 decimals; the default 28-digit
context would round their sums and differences. Tax figures are computed in a
100-digit context so day-by-day figures add up to the year exactly, and are
rounded only for display.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import localcontext
from typing import Final

__all__ = ["TAX_PRECISION", "exact"]

TAX_PRECISION: Final[int] = 100


@contextmanager
def exact() -> Iterator[None]:
    """Run the block in a :data:`TAX_PRECISION`-digit decimal context."""
    with localcontext() as ctx:
        ctx.prec = TAX_PRECISION
        yield
