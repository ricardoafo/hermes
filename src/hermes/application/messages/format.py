"""Number, time and glyph formatting shared by every notification template.

One rule per quantity, so a message reads the same on Telegram and Slack:

- money has two decimals and thousands separators (``€1,234.50``; a quote
  asset other than EUR is shown in euros when the caller holds the official
  rate (:func:`fmt_quote_money`) and otherwise keeps its code,
  ``61,234.50 USDT``, because a notification must not invent a rate);
- percentages carry a sign and one decimal (minus 3.6%), weights are shown as
  a plain percentage (``35.0%``) and drift as percentage points;
- durations are words (``15 min``), never raw seconds;
- times are Europe/Madrid wall clock (``Sat 14:00``), with UTC added only
  where the operator may need it for a log lookup;
- severity is a plain-text glyph, so it reads the same in a chat, a shell
  script and a screen reader.

Money, signs and rounding come from :mod:`hermes.application.console.format`,
the helpers the operator approved for screens. All functions are pure.

Reference: openspec/archive/144-notification-system/change.md (R-2);
    docs/decisions/2026-10-04-notification-templates-v2.md (rules 1-3, 6).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from zoneinfo import ZoneInfo

from hermes.application.console.format import MINUS, fmt_level
from hermes.application.messages._escape import correlation_tail
from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.value_objects.cents import CENT
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.time import MADRID

__all__ = [
    "GLYPH_DOWN",
    "GLYPH_HALF",
    "GLYPH_OK",
    "GLYPH_STOP",
    "GLYPH_UP",
    "SEPARATOR",
    "fmt_asset_qty",
    "fmt_distance_pct",
    "fmt_duration",
    "fmt_money",
    "fmt_pp",
    "fmt_quote_money",
    "fmt_ref",
    "fmt_weight",
    "fmt_when",
    "fmt_when_utc",
    "severity_glyph",
]

GLYPH_UP: Final = "▲"
"""Something opened or went up: an entry, a buy."""
GLYPH_DOWN: Final = "▼"
"""Something closed or went down: an exit, a sell."""
GLYPH_STOP: Final = "■"
"""Needs the operator now: down, critical, rejected."""
GLYPH_HALF: Final = "◐"
"""Degraded or worth a look: warning, drift."""
GLYPH_OK: Final = "●"
"""Fine or recovered: info, back, resumed."""

SEPARATOR: Final = " · "
"""Joins the parts of one line."""

_UTC: Final = ZoneInfo("UTC")
_WEEKDAYS: Final = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TENTH: Final = Decimal("0.1")
_FOUR_PLACES: Final = Decimal("0.0001")
_HUNDRED: Final = Decimal(100)
_ZERO: Final = Decimal(0)
_SECONDS_PER_MINUTE: Final = 60
_SECONDS_PER_HOUR: Final = 3600
_SECONDS_PER_DAY: Final = 86400
_ROUND_TO_MINUTE: Final = timedelta(seconds=30)

_SEVERITY_GLYPHS: Final[dict[AnomalySeverity, str]] = {
    AnomalySeverity.CRITICAL: GLYPH_STOP,
    AnomalySeverity.WARNING: GLYPH_HALF,
    AnomalySeverity.INFO: GLYPH_OK,
}


def severity_glyph(severity: AnomalySeverity) -> str:
    """Return the plain-text glyph for a severity.

    Args:
        severity: The severity to show.

    Returns:
        ``■`` for critical, ``◐`` for warning, ``●`` for info.
    """
    return _SEVERITY_GLYPHS[severity]


def _grouped(value: Decimal, places: Decimal) -> str:
    """Return ``value`` to fixed ``places`` with separators and a minus sign."""
    rounded = value.quantize(places, rounding=ROUND_HALF_UP)
    prefix = MINUS if rounded < _ZERO else ""
    return f"{prefix}{abs(rounded):,}"


def fmt_money(amount: Decimal, currency: Currency) -> str:
    """Return an amount with two decimals, separators and its currency.

    Args:
        amount: The amount in ``currency``.
        currency: Its currency. EUR gets the symbol in front; any other
            currency keeps its code after the number.

    Returns:
        For example ``€1,234.50`` or ``61,234.50 USDT``.
    """
    if currency is Currency.EUR:
        return fmt_level(amount)
    return f"{_grouped(amount, CENT)} {currency.value}"


def fmt_quote_money(amount: Decimal, currency: Currency, eur_rate: Decimal | None) -> str:
    """Return a quote-currency amount in EUR when a rate is known, else as is.

    Args:
        amount: The amount in ``currency``.
        currency: Its currency.
        eur_rate: Units of ``currency`` per one euro (the ECB quotation), or
            ``None`` when no rate is known. Ignored for EUR itself.

    Returns:
        ``€52,410.20`` with a rate, ``61,234.50 USDT`` without one.
    """
    if eur_rate is None or currency is Currency.EUR:
        return fmt_money(amount, currency)
    return fmt_money(amount / eur_rate, Currency.EUR)


def fmt_asset_qty(amount: Decimal, asset: Currency) -> str:
    """Return a quantity with four decimals, separators and its asset code.

    Args:
        amount: Base-asset quantity.
        asset: The asset.

    Returns:
        For example ``0.0040 BTC``.
    """
    return f"{_grouped(amount, _FOUR_PLACES)} {asset.value}"


def fmt_distance_pct(value: Decimal) -> str:
    """Return a signed percentage to one decimal, e.g. ``+1.2%``.

    Args:
        value: The percentage (``-3.6`` for minus 3.6 percent).

    Returns:
        The signed figure; zero carries no sign.
    """
    rounded = value.quantize(_TENTH, rounding=ROUND_HALF_UP)
    sign = "+" if rounded > _ZERO else MINUS if rounded < _ZERO else ""
    return f"{sign}{abs(rounded)}%"


def fmt_weight(ratio: Decimal) -> str:
    """Return a portfolio weight as a percentage to one decimal.

    Args:
        ratio: The weight as a ratio (``0.35`` for 35 percent).

    Returns:
        For example ``35.0%``.
    """
    return f"{_grouped(ratio * _HUNDRED, _TENTH)}%"


def fmt_pp(ratio: Decimal) -> str:
    """Return a gap between two weights in percentage points.

    Args:
        ratio: The gap as a ratio (``0.10`` for ten points).

    Returns:
        For example ``10.0 pp``.
    """
    return f"{_grouped(ratio * _HUNDRED, _TENTH)} pp"


def fmt_duration(seconds: int) -> str:
    """Return a duration as words, truncated to its two largest units.

    Args:
        seconds: Elapsed seconds. A negative value reads as zero.

    Returns:
        ``45 s``, ``15 min``, ``2 h 5 min`` or ``1 d 3 h``.
    """
    total = max(seconds, 0)
    days, rest = divmod(total, _SECONDS_PER_DAY)
    hours, rest = divmod(rest, _SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, _SECONDS_PER_MINUTE)
    if days:
        return f"{days} d {hours} h" if hours else f"{days} d"
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    if minutes:
        return f"{minutes} min"
    return f"{secs} s"


def _to_minute(moment: datetime, zone: ZoneInfo) -> datetime:
    """Return ``moment`` in ``zone`` rounded to the nearest minute.

    A bar closes at ``15:59:59.999``; the operator reads ``16:00``.
    """
    shifted = moment.astimezone(zone) + _ROUND_TO_MINUTE
    return shifted.replace(second=0, microsecond=0)


def fmt_when(moment: datetime) -> str:
    """Return Madrid wall-clock time with the weekday, e.g. ``Sat 14:00``.

    Args:
        moment: A timezone-aware instant.

    Returns:
        Weekday and ``HH:MM`` in Europe/Madrid, rounded to the minute.
    """
    local = _to_minute(moment, MADRID)
    return f"{_WEEKDAYS[local.weekday()]} {local:%H:%M}"


def fmt_when_utc(moment: datetime) -> str:
    """Return Madrid time and the UTC clock, e.g. ``Sat 14:00 (12:00 UTC)``.

    UTC is what the VPS logs use, so the verbose shape adds it for lookups.

    Args:
        moment: A timezone-aware instant.

    Returns:
        The Madrid weekday and time, then the UTC time in brackets.
    """
    utc = _to_minute(moment, _UTC)
    return f"{fmt_when(moment)} ({utc:%H:%M} UTC)"


def fmt_ref(correlation_id: str) -> str:
    """Return the correlation-id tail the way ``hermes logs`` prints it.

    Args:
        correlation_id: The full correlation id.

    Returns:
        An ellipsis and the last six characters, e.g. ``…K7Q2ZP``.
    """
    return f"…{correlation_tail(correlation_id)}"
