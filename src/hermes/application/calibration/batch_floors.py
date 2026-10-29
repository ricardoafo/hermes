"""The bounds a research batch may tighten but never loosen.

:data:`FLOORS` holds the ADR-021 §5 floors of the batch's ``backtest_funnel``
block (ADR-026 §1). :func:`signed_cap` reads a candidate's ADR-029 §4
round-trip cap: ``round_trips_90d_max``, at most :data:`ROUND_TRIP_CAP_MAX`.
Every candidate signs one, except in the batches of
:data:`SIGNED_BEFORE_CAP_ROW`, which parse as they did; ``holding`` stays
optional, and ``holding: under_a_day`` needs the cap in any batch (ADR-029 §5).

Reference: ADR-021 §5; ADR-026 §1; ADR-029 §4, §5; issues #1057, #1240;
openspec/changes/1240-round-trip-cap/change.md.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "CAP_KEYS",
    "FLOORS",
    "HOLDINGS",
    "ROUND_TRIP_CAP_MAX",
    "SIGNED_BEFORE_CAP_ROW",
    "Floor",
    "RoundTripCapError",
    "caps_detail",
    "signed_cap",
]


@dataclass(frozen=True, slots=True)
class Floor:
    """One ADR-021 §5 bound a batch's funnel block may tighten but never loosen."""

    key: str
    bound: float
    at_least: bool
    """True: the value must be >= ``bound``; False: <= ``bound``."""

    def holds(self, value: float) -> bool:
        """Return True when ``value`` is at the floor or stricter."""
        return value >= self.bound if self.at_least else value <= self.bound

    def describe(self) -> str:
        """The bound as the refusal states it, such as ``>= 0.95``."""
        return f"{'>=' if self.at_least else '<='} {self.bound:g}"


FLOORS: Final = (
    Floor("dsr_min", 0.95, at_least=True),
    Floor("pbo_max", 0.05, at_least=False),
    Floor("t_stat_above", 3.0, at_least=True),
    Floor("trade_count_min", 100, at_least=True),
)
"""ADR-021 §5 and ADR-026 §1: DSR >= 0.95, PBO <= 0.05, t above 3, at least 100 trades."""

ROUND_TRIP_CAP_MAX: Final = 250
"""ADR-029 §4: half of ADR-002's 500. A batch may sign a lower cap, never a higher one."""

SIGNED_BEFORE_CAP_ROW: Final = frozenset({"swing-b1", "swing-b2"})
"""Batch ids signed before ADR-029 §4's row existed; their candidates sign no cap and get no row."""

_HOLDING: Final = "holding"
_CAP: Final = "round_trips_90d_max"
CAP_KEYS: Final = frozenset({_HOLDING, _CAP})
"""The optional candidate keys this module reads."""

_UNDER_A_DAY: Final = "under_a_day"
HOLDINGS: Final = (_UNDER_A_DAY, "a_day_or_more")


class RoundTripCapError(ValueError):
    """A candidate's ``holding`` or ``round_trips_90d_max`` breaks ADR-029 §4 or §5."""


def signed_cap(fields: Mapping[str, object], where: str, *, required: bool) -> int | None:
    """Return the candidate's signed round-trip cap, or ``None`` when it signs none.

    Args:
        fields: The candidate's front-matter keys.
        where: How a refusal names the candidate, such as ``candidates[1]``.
        required: Whether the candidate must sign a cap: true in every batch
            outside :data:`SIGNED_BEFORE_CAP_ROW`.

    Returns:
        The cap, from 1 to :data:`ROUND_TRIP_CAP_MAX`; ``None`` when the cap
        is not required, not signed and the candidate does not hold under a day.

    Raises:
        RoundTripCapError: ``holding`` is not one of :data:`HOLDINGS`, a
            required cap or the cap of a candidate holding under a day is
            missing, or the cap is not a positive integer or is above
            :data:`ROUND_TRIP_CAP_MAX`.
    """
    holding = fields.get(_HOLDING)
    if holding is not None and holding not in HOLDINGS:
        raise RoundTripCapError(
            f"{where}.{_HOLDING} must be one of {list(HOLDINGS)}, got {holding!r}"
        )
    cap = fields.get(_CAP)
    if cap is None:
        if holding == _UNDER_A_DAY:
            raise RoundTripCapError(f"{where} holds under a day but signs no {_CAP} (ADR-029 §4)")
        if required:
            raise RoundTripCapError(f"{where} signs no {_CAP} (ADR-029 §4)")
        return None
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise RoundTripCapError(f"{where}.{_CAP} must be a positive integer, got {cap!r}")
    if cap > ROUND_TRIP_CAP_MAX:
        raise RoundTripCapError(
            f"{where}.{_CAP} {cap} is looser than ADR-029 §4 (<= {ROUND_TRIP_CAP_MAX})"
        )
    return cap


def caps_detail(caps: Sequence[int | None]) -> str:
    """The ``funnel_floors`` line's note on signed caps; empty when no candidate signs one."""
    return "" if all(c is None for c in caps) else f"; round trips per 90 days at most {list(caps)}"
