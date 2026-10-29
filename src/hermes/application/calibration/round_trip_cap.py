"""The ADR-029 §4 gate row: a candidate's Train run holds at most its cap of round trips in 90 days.

ADR-002's tax guard pauses new signals above 500 trades in a rolling 90 days,
account-wide. ADR-029 §4 gives each candidate at most half of that room: its
Train run fails the gate if any rolling 90-day window holds more than 250
round trips. The cap is signed per candidate in its batch
(``round_trips_90d_max``, read by :mod:`.batch_floors`); a candidate that
signs none gets no row, so batches signed before the row are judged as they
were.

A round trip sits in the window that holds its opening fill, the time the
shared round-trip loop records (:class:`~hermes.domain.portfolio.round_trips.ClosedRoundTrip`).
Windows are closed at both ends, as the tax guard's are: trips opened exactly
90 days apart share one. Only trips opened on or before ``train_end`` count,
so the row judges the Train run on a Train + Validation run too.

Reference: ADR-029 §4; ADR-002 §Decision 6; ADR-026 §1; issue #1240;
openspec/changes/1240-round-trip-cap/change.md.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Final

from hermes.application.calibration.certification_gates import GateCheck

__all__ = [
    "CAP_WINDOW",
    "ROUND_TRIP_CAP_CHECK",
    "busiest_window",
    "round_trip_cap_row",
]

ROUND_TRIP_CAP_CHECK: Final = "round_trip_cap"
"""The row's check name: one token, so a job summary keeps only name and verdict."""

CAP_WINDOW: Final = timedelta(days=90)

_NOT_COMPUTED: Final = "not computed"


def busiest_window(opened_at: Sequence[datetime], window: timedelta) -> int:
    """Return the most round trips any one closed window of length ``window`` holds.

    Args:
        opened_at: When each round trip opened, in any order; repeats count each time.
        window: The window's length; a trip at its end is inside it.

    Returns:
        The largest count, or 0 with no round trips.
    """
    times = sorted(opened_at)
    busiest = 0
    first = 0
    for last, closing in enumerate(times):
        while closing - times[first] > window:
            first += 1
        busiest = max(busiest, last - first + 1)
    return busiest


def round_trip_cap_row(
    opened_at: Sequence[datetime] | None, *, cap: int, train_end: date
) -> GateCheck:
    """Judge the ADR-029 §4 cap on the round trips a run opened during Train.

    Args:
        opened_at: When each of the run's round trips opened, or ``None`` when
            the evidence does not say; ``None`` fails the row.
        cap: The candidate's signed ``round_trips_90d_max``.
        train_end: The last Train day; trips opened after it are not counted.

    Returns:
        The ``round_trip_cap`` row: PASS when no 90-day window holds more than ``cap``.
    """
    requirement = f"<= {cap} round trips in any {CAP_WINDOW.days} days of Train"
    if opened_at is None:
        return GateCheck(ROUND_TRIP_CAP_CHECK, requirement, _NOT_COMPUTED, passed=False)
    train = [t for t in opened_at if t.date() <= train_end]
    busiest = busiest_window(train, CAP_WINDOW)
    return GateCheck(ROUND_TRIP_CAP_CHECK, requirement, str(busiest), passed=busiest <= cap)
