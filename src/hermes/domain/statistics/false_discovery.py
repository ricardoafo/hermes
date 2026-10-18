"""False-discovery control: the Benjamini-Hochberg step-up procedure.

A research batch tests several candidates at once, so a DSR that clears its
floor by luck is more likely the more candidates there are. BH keeps the
expected share of false discoveries among the survivors at or below q.

Reference: ADR-026 §5.2; ADR-021 §5 ("combining results"); Benjamini and
Hochberg (1995), "Controlling the False Discovery Rate".
"""

from collections.abc import Sequence
from fractions import Fraction

__all__ = ["benjamini_hochberg"]


def benjamini_hochberg(p_values: Sequence[Fraction], *, q: Fraction) -> tuple[bool, ...]:
    """Return which hypotheses BH rejects at level ``q``.

    Sorts the m p-values, finds the largest rank k with ``p_(k) <= k * q / m``
    and rejects every hypothesis whose p-value is at most ``p_(k)``. The
    decision for each p-value does not depend on the order it is given in.
    Exact fractions, because a p-value sitting on its line is the common case
    at a signed floor: in floats, 1 - 0.95 is 0.05000000000000004 > 0.05.

    Args:
        p_values: One p-value per hypothesis, each in [0, 1].
        q: The false-discovery level, 0 < q < 1.

    Returns:
        ``True`` for each rejected hypothesis, in the order of ``p_values``.

    Raises:
        ValueError: ``q`` outside (0, 1) or a p-value outside [0, 1].
    """
    if not 0.0 < q < 1.0:
        raise ValueError(f"q must be in (0, 1), got {q}")
    if any(not 0.0 <= p <= 1.0 for p in p_values):
        raise ValueError(f"p-values must be in [0, 1], got {list(p_values)}")
    m = len(p_values)
    threshold: Fraction | None = None
    for rank, p in enumerate(sorted(p_values), start=1):
        if p <= rank * q / m:
            threshold = p
    return tuple(threshold is not None and p <= threshold for p in p_values)
