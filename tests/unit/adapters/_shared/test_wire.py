"""Invariant tests for the wire helpers every HTTP adapter shares (issue #1177).

Invariants:

- ``wire_decimal`` returns ``Decimal(str(raw))`` for every finite number or
  numeric string, and refuses a bool, NaN or infinity with the caller's own
  error type, naming the value;
- with ``numbers=False`` it refuses JSON numbers, for endpoints that quote;
- ``binance_symbol`` is base then quote with no separator;
- ``percent_to_bps`` rounds to whole basis points and refuses a negative fee;
- the window and HTTP config checks refuse what they name;
- no wire adapter parses numbers, builds Binance symbols or owns a token
  bucket by hand again (the guard against the copies drifting back).
"""

import math
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters._shared.wire import (
    binance_symbol,
    percent_to_bps,
    validate_http_config,
    validate_utc_window,
    wire_decimal,
)
from hermes.domain.errors import ExchangeError, FxRateError, KrakenPublicError
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_ERRORS: tuple[type[Exception], ...] = (ExchangeError, KrakenPublicError, FxRateError)
_NON_FINITE_TEXT = ("NaN", "nan", "sNaN", "Infinity", "-Infinity", "inf", "-inf")
_SRC = Path(__file__).resolve().parents[4] / "src" / "hermes" / "adapters"
_WIRE_PACKAGES = ("exchanges", "market_data", "fx", "healthchecks")

errors = st.sampled_from(_ERRORS)
non_finite = st.one_of(
    st.sampled_from(_NON_FINITE_TEXT),
    st.sampled_from([math.nan, math.inf, -math.inf]),
)


@given(error=errors, raw=st.booleans())
def test_a_bool_is_refused_with_the_callers_error(error: type[Exception], raw: bool) -> None:
    """A JSON true or false where a number belongs raises the caller's error, not a 1 or 0."""
    with pytest.raises(error, match=r"price is not numeric: (True|False)"):
        wire_decimal(raw, field="price", error=error)


@given(error=errors, raw=non_finite)
def test_a_non_finite_value_is_refused_with_the_callers_error(
    error: type[Exception], raw: str | float
) -> None:
    """NaN or infinity, as text or a JSON float, raises the caller's error naming the value."""
    with pytest.raises(error, match="price is not a finite number") as raised:
        wire_decimal(raw, field="price", error=error)

    assert repr(raw) in str(raised.value)


@given(error=errors, raw=st.text())
def test_text_that_is_not_a_decimal_is_refused_or_parsed_exactly(
    error: type[Exception], raw: str
) -> None:
    """Any string either parses to exactly ``Decimal(raw)`` or raises the caller's error."""
    try:
        expected = Decimal(raw)
    except ArithmeticError:
        with pytest.raises(error):
            wire_decimal(raw, field="qty", error=error)
        return
    if not expected.is_finite():
        return
    assert wire_decimal(raw, field="qty", error=error) == expected


@given(raw=st.floats(allow_nan=False, allow_infinity=False))
def test_a_json_float_goes_through_its_shortest_text(raw: float) -> None:
    """A float is read as ``Decimal(str(raw))``, never as its binary expansion."""
    assert wire_decimal(raw, field="rate", error=ExchangeError) == Decimal(str(raw))


@given(raw=st.integers())
def test_a_json_integer_is_read_exactly(raw: int) -> None:
    """A JSON integer (not a bool) parses to the same integer as a Decimal."""
    assert wire_decimal(raw, field="ts", error=ExchangeError) == Decimal(raw)


@pytest.mark.parametrize("raw", [1, 1.5, None, ["1"], {"p": "1"}])
def test_strings_only_refuses_anything_but_a_string(raw: object) -> None:
    """With ``numbers=False`` a JSON number, null or container is refused as not a string."""
    with pytest.raises(ExchangeError, match="depth bids price is not a string"):
        wire_decimal(raw, field="depth bids price", error=ExchangeError, numbers=False)


@pytest.mark.parametrize("raw", [None, ["1"], {"p": "1"}, b"1"])
def test_a_non_scalar_is_refused_as_not_numeric(raw: object) -> None:
    """Null, a list, a mapping or bytes is refused before any parse is tried."""
    with pytest.raises(KrakenPublicError, match="fee is not numeric"):
        wire_decimal(raw, field="fee", error=KrakenPublicError)


@given(
    base=st.sampled_from(list(Currency)),
    quote=st.sampled_from(list(Currency)),
)
def test_binance_symbol_is_base_then_quote(base: Currency, quote: Currency) -> None:
    """The Binance symbol is the two codes joined with nothing between them."""
    if base is quote:
        return
    assert binance_symbol(Pair(base=base, quote=quote)) == f"{base.value}{quote.value}"


@pytest.mark.parametrize(
    ("percent", "bps"),
    [("0", 0), ("0.26", 26), ("0.1", 10), ("0.004", 0), ("0.005", 0), ("0.015", 2)],
)
def test_percent_to_bps_rounds_half_even_to_whole_points(percent: str, bps: int) -> None:
    """Percent times 100, rounded with the decimal context's half-even rule."""
    assert percent_to_bps(Decimal(percent), field="fee", error=ExchangeError) == bps


def test_percent_to_bps_refuses_a_negative_fee() -> None:
    """A negative fee percent is a malformed payload, raised with the caller's error."""
    with pytest.raises(KrakenPublicError, match=r"fee must be >= 0, got -0.1"):
        percent_to_bps(Decimal("-0.1"), field="fee", error=KrakenPublicError)


def test_percent_to_bps_turns_an_overflow_into_the_callers_error() -> None:
    """A percent too large for the decimal context raises the caller's error, not Overflow."""
    with pytest.raises(ExchangeError, match="fee is out of range"):
        percent_to_bps(Decimal("9e999999"), field="fee", error=ExchangeError)


_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("since", "until", "message"),
    [
        (_AT.replace(tzinfo=None), _AT + timedelta(hours=1), "since must be UTC-aware"),
        (_AT, (_AT + timedelta(hours=1)).astimezone(timezone(timedelta(hours=2))), "until must"),
        (_AT, _AT, r"until \(.*\) must be > since"),
    ],
    ids=["naive-since", "non-utc-until", "empty-window"],
)
def test_a_bad_window_is_refused_naming_the_caller(
    since: datetime, until: datetime, message: str
) -> None:
    """A naive or non-UTC bound, or an empty window, raises ValueError naming the method."""
    with pytest.raises(ValueError, match=rf"^Adapter\.fetch: {message}"):
        validate_utc_window(since, until, caller="Adapter.fetch")


@pytest.mark.parametrize(
    ("url", "timeout", "message"),
    [
        ("http://api.example", 5.0, r"Cfg\.rest_url must start with https://"),
        ("https://api.example", 0.0, r"Cfg\.timeout_sec must be > 0, got 0.0"),
    ],
    ids=["plaintext", "zero-timeout"],
)
def test_http_config_refuses_plaintext_or_no_timeout(
    url: str, timeout: float, message: str
) -> None:
    """A plaintext URL or a non-positive timeout raises ValueError naming the config field."""
    with pytest.raises(ValueError, match=message):
        validate_http_config("Cfg", url=url, timeout_sec=timeout, url_field="rest_url")


def _wire_sources() -> dict[str, str]:
    return {
        str(path.relative_to(_SRC)): path.read_text(encoding="utf-8")
        for package in _WIRE_PACKAGES
        for path in sorted((_SRC / package).rglob("*.py"))
    }


@pytest.mark.parametrize(
    ("pattern", "allowed"),
    [
        (r"InvalidOperation|\.is_finite\(\)", set()),
        (r"^class RateLimit(Config|er)\b", set()),
        # Kraken's own symbol fallback happens to concatenate too; it is not a Binance symbol.
        (r"pair\.base\.value\}\{pair\.quote\.value\}", {"exchanges/kraken_public.py"}),
    ],
    ids=["hand-rolled-decimal-parse", "own-token-bucket", "hand-built-binance-symbol"],
)
def test_no_wire_adapter_grows_its_own_copy_again(pattern: str, allowed: set[str]) -> None:
    """Wire adapters parse numbers, build symbols and rate-limit only through adapters/_shared."""
    found = {
        name for name, text in _wire_sources().items() if re.search(pattern, text, re.MULTILINE)
    }

    assert found ^ allowed == set()
