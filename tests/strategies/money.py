"""Hypothesis strategies for the Money value object.

Composable building blocks per code-conventions.md §8 (Testing patterns).
Register via ``st.register_type_strategy`` so hypothesis auto-generates
``Money`` values for any dataclass field of that type.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import strategies as st

from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money


@st.composite
def positive_decimal_strategy(
    draw: st.DrawFn,
    *,
    max_digits: int = 18,
    places: int = 8,
) -> Decimal:
    """Draw a non-negative Decimal with bounded precision.

    Args:
        draw: Hypothesis draw function.
        max_digits: Maximum total significant digits.
        places: Number of decimal places.

    Returns:
        A non-negative Decimal value.

    """
    return draw(
        st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("9" * (max_digits - places) + "." + "9" * places),
            allow_nan=False,
            allow_infinity=False,
            places=places,
        )
    )


@st.composite
def currency_strategy(draw: st.DrawFn) -> Currency:
    """Draw a random Currency from the allowlist.

    Args:
        draw: Hypothesis draw function.

    Returns:
        A random ``Currency`` enum member.

    """
    return draw(st.sampled_from(list(Currency)))


@st.composite
def money_strategy(
    draw: st.DrawFn,
    *,
    currency: Currency | None = None,
) -> Money:
    """Draw a random Money value.

    Args:
        draw: Hypothesis draw function.
        currency: If provided, fix the currency; otherwise draw randomly.

    Returns:
        A ``Money`` instance with a non-negative amount.

    """
    amount = draw(positive_decimal_strategy())
    cur = currency or draw(currency_strategy())
    return Money(amount=amount, currency=cur)


# Register so hypothesis auto-generates Money for @given(money_a=...) style
st.register_type_strategy(Money, money_strategy())
