"""The tax year: the calendar year in Europe/Madrid time (ADR-016 item 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo

__all__ = ["tax_year_bounds", "tax_year_of"]

_MADRID: Final = ZoneInfo("Europe/Madrid")


def tax_year_of(at: datetime) -> int:
    """Return the tax year an instant belongs to.

    Args:
        at: Timezone-aware instant.

    Returns:
        The Madrid calendar year of ``at``; 23:30 UTC on 31 December is
        already the next year.

    Raises:
        ValueError: When ``at`` is naive.
    """
    if at.tzinfo is None:
        raise ValueError(f"tax_year_of needs an aware datetime, got naive {at!r}")
    return at.astimezone(_MADRID).year


def tax_year_bounds(year: int) -> tuple[datetime, datetime]:
    """Return the UTC half-open interval ``[start, end)`` of a tax year.

    Args:
        year: Tax year.

    Returns:
        Midnight 1 January Madrid of ``year`` and of ``year + 1``, in UTC.
    """
    start = datetime(year, 1, 1, tzinfo=_MADRID).astimezone(UTC)
    end = datetime(year + 1, 1, 1, tzinfo=_MADRID).astimezone(UTC)
    return start, end
