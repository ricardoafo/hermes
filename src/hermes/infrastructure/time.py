"""UTC-internal / Europe/Madrid-at-edges time helpers.

Every timestamp inside hermes is UTC-aware. These helpers centralise the
conversion logic so no caller ever touches ``zoneinfo`` directly or falls back
to ``timedelta(hours=...)`` (which silently breaks around DST).

Reference: code-conventions.md §9 — Time handling — UTC internally, Madrid at edges.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from hermes.domain.value_objects.time import MADRID

__all__ = ["from_madrid", "now_utc", "to_madrid"]

_UTC = ZoneInfo("UTC")


def now_utc() -> datetime:
    """Return the current instant as a timezone-aware UTC datetime.

    Returns:
        A ``datetime`` with ``tzinfo=ZoneInfo("UTC")``. Never naive.

    Reference: code-conventions.md §9.
    """
    return datetime.now(tz=_UTC)


def to_madrid(dt: datetime) -> datetime:
    """Convert a UTC-aware datetime to Europe/Madrid wall-clock time.

    Handles CET (UTC+1) / CEST (UTC+2) DST transitions automatically via
    ``zoneinfo.ZoneInfo``. Use this ONLY at output edges (CLI display, tax
    reports) — never store the result in the ledger or domain events.

    Args:
        dt: Timezone-aware datetime. Should be UTC, but any tz-aware value is
            accepted and converted correctly.

    Returns:
        Equivalent datetime in Europe/Madrid timezone.

    Reference: code-conventions.md §9.
    """
    return dt.astimezone(MADRID)


def from_madrid(dt: datetime) -> datetime:
    """Convert a Europe/Madrid datetime to UTC.

    Use when parsing user-provided wall-clock times (e.g. CLI input, tax
    report dates expressed in Madrid local time) back to the internal UTC
    representation.

    Args:
        dt: Timezone-aware datetime in Europe/Madrid (or any tz-aware value).

    Returns:
        Equivalent datetime in UTC.

    Reference: code-conventions.md §9.
    """
    return dt.astimezone(_UTC)
