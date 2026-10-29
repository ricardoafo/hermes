"""Hypothesis strategies for the Price value object."""

from decimal import Decimal

from hypothesis import strategies as st

from hermes.domain.value_objects.price import Price
from tests.strategies.pair import pair_strategy


@st.composite
def price_strategy(draw: st.DrawFn) -> Price:
    """Draw a random Price with a positive amount and valid pair.

    Args:
        draw: Hypothesis draw function.

    Returns:
        A valid ``Price`` instance.

    """
    # Use a min_value > 0 for prices — a price of 0 is degenerate but valid
    amount = draw(
        st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("999999999.99999999"),
            allow_nan=False,
            allow_infinity=False,
            places=8,
        )
    )
    pair = draw(pair_strategy())
    return Price(amount=amount, pair=pair)


st.register_type_strategy(Price, price_strategy())
