"""The one Europe/Madrid zone and the one UTC check (issue #1180).

Hermes keeps every instant in UTC and shows Madrid at the edges
(code-conventions §9). Before this module the zone was built in eleven
places and the UTC check in three, and the three checks disagreed: two let a
Madrid-aware time through. Every caller now reads these two names, and a
source scan (``tests/unit/domain/value_objects/test_home_modules.py``) fails
when a copy comes back.
"""

from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

__all__ = ["MADRID", "require_utc"]

MADRID: Final = ZoneInfo("Europe/Madrid")

_ZERO: Final = timedelta(0)


def require_utc(value: datetime, *, name: str) -> None:
    """Refuse a naive datetime or one in any zone but UTC.

    The zone itself must be fixed at offset zero, so a London time in winter
    (offset zero that day, a different zone) is refused as well.

    Args:
        value: The datetime to check.
        name: The field or argument name for the error message.

    Raises:
        ValueError: ``value`` is naive or its zone is not UTC.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(None) != _ZERO:
        raise ValueError(f"{name} must be UTC-aware (a timezone-aware UTC datetime), got {value!r}")
