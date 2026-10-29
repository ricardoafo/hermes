"""The one cent grid and its rounding rule (issue #1180).

Every EUR or USDT figure hermes shows or files is rounded to the cent, half
up, the way a Spanish tax form and a bank statement round. Before this
module nine files built their own ``Decimal("0.01")``, and two of them rounded
half to even, so one amount could read 0.12 on one console screen and 0.13
on the next. A source scan (``tests/unit/domain/value_objects/test_home_modules.py``)
fails when a copy comes back.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

__all__ = ["CENT", "cents_text", "to_cents"]

CENT: Final[Decimal] = Decimal("0.01")


def to_cents(amount: Decimal) -> Decimal:
    """Return ``amount`` rounded to the cent, half up.

    Args:
        amount: Any finite amount.

    Returns:
        The amount with exactly two decimals, for example ``Decimal("0.13")``
        for ``Decimal("0.125")``.
    """
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def cents_text(amount: Decimal) -> str:
    """Return ``amount`` rounded to the cent as plain text, ``-1234.50`` style.

    Args:
        amount: Any finite amount.

    Returns:
        Two decimals, a point, no thousands separator and no currency.
    """
    return str(to_cents(amount))
