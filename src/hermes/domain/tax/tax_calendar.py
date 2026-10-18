"""The Spanish tax calendar: Europe/Madrid dates and years (ADR-016 decision 2).

Every tax rule reads time as a Madrid calendar date, so a sale at 23:30 UTC on
31 December is a 1 January sale and falls in the next tax year. The tax
screen and the Modelo 100 pack both read these functions, so the two surfaces
cannot disagree on which day or year an instant belongs to (issue #1171).

Reference: ADR-016 decisions 2 and 8; repo audit 18 Oct, D-1 and D-2.
"""

import calendar
from datetime import UTC, date, datetime, time

from hermes.domain.value_objects.time import MADRID

__all__ = [
    "MADRID",
    "add_months",
    "madrid_date",
    "madrid_midnight",
    "tax_year_bounds",
    "tax_year_of",
]


def _aware(at: datetime, caller: str) -> datetime:
    if at.tzinfo is None:
        raise ValueError(f"{caller} needs a timezone-aware datetime, got naive {at!r}")
    return at


def madrid_date(at: datetime) -> date:
    """Return the Madrid calendar date of an instant.

    Args:
        at: Timezone-aware instant.

    Returns:
        The date on a Madrid wall calendar at ``at``.

    Raises:
        ValueError: When ``at`` is naive.
    """
    return _aware(at, "madrid_date").astimezone(MADRID).date()


def madrid_midnight(day: date) -> datetime:
    """Return the UTC instant at which ``day`` starts in Madrid.

    Args:
        day: A Madrid calendar date.

    Returns:
        00:00 of ``day`` in Madrid, as a UTC datetime.
    """
    return datetime.combine(day, time(), tzinfo=MADRID).astimezone(UTC)


def tax_year_of(at: datetime) -> int:
    """Return the tax year of an instant: its Madrid calendar year.

    Args:
        at: Timezone-aware instant.

    Returns:
        The Madrid calendar year of ``at``; 23:30 UTC on 31 December is
        already the next year.

    Raises:
        ValueError: When ``at`` is naive.
    """
    return _aware(at, "tax_year_of").astimezone(MADRID).year


def tax_year_bounds(year: int) -> tuple[datetime, datetime]:
    """Return the UTC half-open interval ``[start, end)`` of a tax year.

    Args:
        year: Tax year.

    Returns:
        Midnight 1 January Madrid of ``year`` and of ``year + 1``, in UTC.
    """
    return madrid_midnight(date(year, 1, 1)), madrid_midnight(date(year + 1, 1, 1))


def add_months(day: date, months: int) -> date:
    """Shift ``day`` by whole calendar months, clamping to the month's last day.

    Args:
        day: Start date.
        months: Months to add; negative goes back.

    Returns:
        The shifted date (31 March minus one month is 28 or 29 February).
    """
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))
