"""Wire helpers every HTTP adapter shares: numbers, symbols, windows and config.

Each adapter used to carry its own copy of these, and the copies drifted
(two accepted a JSON ``true`` as a number, each raised a different error
shape). One copy here keeps the rule the same on every price and fee path
(issue #1177). Each caller still raises its own typed error, which is what
its retry and degraded-feed handling catches.

Reference: ADR-003 §04, code-conventions.md §6 (``Decimal(str(x))``).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, Overflow

from hermes.adapters.endpoint import is_loopback
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "WireError",
    "binance_symbol",
    "percent_to_bps",
    "validate_http_config",
    "validate_utc_window",
    "wire_decimal",
]

type WireError = Callable[[str], Exception]
"""Builds the caller's own exception from a message, e.g. ``ExchangeError``."""


def wire_decimal(raw: object, *, field: str, error: WireError, numbers: bool = True) -> Decimal:
    """Return a wire value as a finite :class:`~decimal.Decimal`.

    A JSON number is read through ``str`` so a float's binary expansion
    never reaches the domain. ``bool`` is refused even though it is an
    ``int``: a ``true`` where a price belongs is a malformed payload.

    Args:
        raw: The decoded JSON (or CSV) value.
        field: Names the value in the error message, with any context the
            caller wants (endpoint, symbol, source file).
        error: Builds the caller's own exception from the message.
        numbers: Also accept JSON numbers. ``False`` accepts strings only,
            for endpoints that always quote their numbers.

    Returns:
        The finite decimal value.

    Raises:
        Exception: Whatever ``error`` builds, when ``raw`` is a bool, of the
            wrong type, not a decimal, NaN or infinite.
    """
    accepted: tuple[type, ...] = (str, int, float) if numbers else (str,)
    if isinstance(raw, bool) or not isinstance(raw, accepted):
        kind = "numeric" if numbers else "a string"
        raise error(f"{field} is not {kind}: {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise error(f"{field} is not a decimal: {raw!r}") from exc
    if not value.is_finite():
        raise error(f"{field} is not a finite number: {raw!r}")
    return value


def binance_symbol(pair: Pair) -> str:
    """Return the Binance symbol for ``pair``: base then quote, no separator (``BTCUSDT``)."""
    return f"{pair.base.value}{pair.quote.value}"


def percent_to_bps(percent: Decimal, *, field: str, error: WireError) -> int:
    """Convert a percent fee (``0.26`` means 0.26%) to whole basis points.

    Venues publish fee percents to two decimal places, so the result rounds
    to the nearest basis point to drop any float artefact.

    Args:
        percent: The fee percent.
        field: Names the value in the error message.
        error: Builds the caller's own exception from the message.

    Returns:
        The fee in basis points (``0.26`` gives ``26``).

    Raises:
        Exception: Whatever ``error`` builds, for a negative percent or one
            too large for the decimal context.
    """
    if percent < 0:
        raise error(f"{field} must be >= 0, got {percent}")
    try:
        return int((percent * 100).to_integral_value())
    except Overflow as exc:
        raise error(f"{field} is out of range: {percent}") from exc


def validate_utc_window(since: datetime, until: datetime, *, caller: str) -> None:
    """Refuse a fetch window that is not UTC-aware or does not move forward.

    Args:
        since: Inclusive lower bound.
        until: Exclusive upper bound.
        caller: Names the method in the error, e.g. ``"BinanceKlinesAdapter.fetch"``.

    Raises:
        ValueError: On a naive or non-UTC bound, or ``until <= since``.
    """
    for label, value in (("since", since), ("until", until)):
        if value.tzinfo is None or value.tzinfo.utcoffset(None) != UTC.utcoffset(None):
            raise ValueError(f"{caller}: {label} must be UTC-aware")
    if until <= since:
        raise ValueError(f"{caller}: until ({until!r}) must be > since ({since!r})")


def validate_http_config(
    owner: str, *, url: str, timeout_sec: float, url_field: str = "base_url"
) -> None:
    """Refuse a plaintext base URL or a non-positive timeout in an adapter config.

    Plain ``http://`` is accepted on a loopback host, where a lower
    environment's fake answers and nothing crosses the network; the config
    loader keeps loopback out of staging and prod (ADR-024 §4).

    Args:
        owner: The config class name, used in the error.
        url: The base URL to check.
        timeout_sec: The request timeout to check.
        url_field: The URL attribute's name, used in the error.

    Raises:
        ValueError: ``url`` is neither ``https://`` nor ``http://`` on a
            loopback host, or ``timeout_sec <= 0``.
    """
    if not url.startswith("https://") and not (url.startswith("http://") and is_loopback(url)):
        raise ValueError(
            f"{owner}.{url_field} must start with https://, or http:// on a loopback host, "
            f"got {url!r}"
        )
    if timeout_sec <= 0:
        raise ValueError(f"{owner}.timeout_sec must be > 0, got {timeout_sec}")
