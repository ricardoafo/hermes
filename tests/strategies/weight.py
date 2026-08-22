"""Hypothesis strategies for the Weight value object."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import strategies as st

from hermes.domain.value_objects.weight import Weight


@st.composite
def weight_strategy(draw: st.DrawFn) -> Weight:
    """Draw a random Weight in [0, 1].

    Args:
        draw: Hypothesis draw function.

    Returns:
        A valid ``Weight`` instance with value in [0, 1].

    """
    value = draw(
        st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("1"),
            allow_nan=False,
            allow_infinity=False,
            places=8,
        )
    )
    return Weight(value=value)


st.register_type_strategy(Weight, weight_strategy())
