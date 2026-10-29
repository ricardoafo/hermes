"""Hypothesis strategies for the Pair value object."""

from hypothesis import strategies as st

from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair


@st.composite
def pair_strategy(draw: st.DrawFn) -> Pair:
    """Draw a random Pair with distinct base and quote currencies.

    Args:
        draw: Hypothesis draw function.

    Returns:
        A valid ``Pair`` instance.

    """
    currencies = list(Currency)
    base = draw(st.sampled_from(currencies))
    remaining = [c for c in currencies if c != base]
    quote = draw(st.sampled_from(remaining))
    return Pair(base=base, quote=quote)


st.register_type_strategy(Pair, pair_strategy())
