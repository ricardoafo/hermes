"""Hypothesis strategies for the Quantity value object."""

from hypothesis import strategies as st

from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.quantity import Quantity
from tests.strategies.money import positive_decimal_strategy


@st.composite
def quantity_strategy(
    draw: st.DrawFn,
    *,
    asset: Currency | None = None,
) -> Quantity:
    """Draw a random Quantity with a non-negative amount.

    Args:
        draw: Hypothesis draw function.
        asset: If provided, fix the asset currency; otherwise draw randomly.

    Returns:
        A valid ``Quantity`` instance.

    """
    amount = draw(positive_decimal_strategy())
    cur = asset or draw(st.sampled_from(list(Currency)))
    return Quantity(amount=amount, asset=cur)


st.register_type_strategy(Quantity, quantity_strategy())
