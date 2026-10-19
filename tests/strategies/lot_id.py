"""Hypothesis strategies for the LotId value object."""

from hypothesis import strategies as st

from hermes.domain.value_objects.lot_id import LotId


@st.composite
def lot_id_strategy(draw: st.DrawFn) -> LotId:
    """Draw a valid LotId by generating a fresh ULID.

    Hypothesis requires at least one ``draw`` call inside a ``@st.composite``
    function; we draw a throwaway boolean to satisfy that contract while the
    actual ULID is generated deterministically via ``LotId.new()``.

    Args:
        draw: Hypothesis draw function.

    Returns:
        A valid ``LotId`` with a fresh ULID value.

    """
    _ = draw(st.booleans())
    return LotId.new()


st.register_type_strategy(LotId, lot_id_strategy())
