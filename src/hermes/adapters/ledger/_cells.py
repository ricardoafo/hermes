"""Read one stored ledger cell as a decimal or a time, refusing what Hermes never writes.

SQLite keeps any type in any column, so a hand edit, a bad migration or a
disk fault can leave a NULL, a blob, ``NaN`` or a naive time where a reader
expects a decimal string or an ISO time with an offset. These helpers raise
``ValueError`` for such a cell; each reader turns that into ``LedgerError``,
the only error its callers handle (issue #842).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

__all__ = ["date_cell", "decimal_cell", "optional_decimal_cell", "time_cell"]


def decimal_cell(raw: object) -> Decimal:
    """Return the finite decimal stored in ``raw`` (text or a number).

    A float is read through ``str`` so its binary expansion never enters
    money maths.

    Raises:
        ValueError: ``raw`` is not a number, or is NaN or infinite.
    """
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise ValueError(f"stored value is not a decimal: {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise ValueError(f"stored value is not a decimal: {raw!r}") from None
    if not value.is_finite():
        raise ValueError(f"stored value is not a finite number: {raw!r}")
    return value


def optional_decimal_cell(raw: object) -> Decimal | None:
    """Return :func:`decimal_cell` of ``raw``, keeping ``None`` as ``None``."""
    return None if raw is None else decimal_cell(raw)


def time_cell(raw: object) -> datetime:
    """Return the timezone-aware time stored in ``raw`` as ISO 8601 text.

    Raises:
        ValueError: ``raw`` is not ISO 8601 text, or carries no UTC offset.
    """
    if not isinstance(raw, str):
        raise ValueError(f"stored time is not text: {raw!r}")
    moment = datetime.fromisoformat(raw)
    if moment.utcoffset() is None:
        raise ValueError(f"stored time has no UTC offset: {raw!r}")
    return moment


def date_cell(raw: object) -> date:
    """Return the date stored in ``raw`` as ISO 8601 text.

    Raises:
        ValueError: ``raw`` is not an ISO 8601 date.
    """
    if not isinstance(raw, str):
        raise ValueError(f"stored date is not text: {raw!r}")
    return date.fromisoformat(raw)
