"""Tests for the notification formatters.

Invariants protected here:

- money, quantities and percentages always carry fixed precision and
  thousands separators, whatever the Decimal;
- negative figures use the typographic minus and zero carries no sign;
- durations read as words, never raw seconds;
- times are Madrid wall clock (DST included) rounded to the minute, with UTC
  added only by the verbose form;
- the ref is the ellipsis and the last six characters of the correlation id.

Reference: openspec/archive/144-notification-system/change.md (R-2).
"""

# ruff: noqa: RUF001 - the typographic minus sign is the character under test

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.messages.format import (
    GLYPH_HALF,
    GLYPH_OK,
    GLYPH_STOP,
    fmt_asset_qty,
    fmt_distance_pct,
    fmt_duration,
    fmt_money,
    fmt_pp,
    fmt_quote_money,
    fmt_ref,
    fmt_weight,
    fmt_when,
    fmt_when_utc,
    severity_glyph,
)
from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.value_objects.currency import Currency

_AMOUNTS = st.decimals(
    min_value=Decimal("-1000000000"), max_value=Decimal("1000000000"), allow_nan=False
)
_WEEKDAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
_GROUPED_TWO = re.compile(r"^−?\d{1,3}(,\d{3})*\.\d{2}$")
_GROUPED_FOUR = re.compile(r"^−?\d{1,3}(,\d{3})*\.\d{4}$")


@given(amount=_AMOUNTS)
def test_usdt_money_always_has_two_decimals_and_separators(amount: Decimal) -> None:
    """Any quote-asset amount renders as grouped digits with exactly two decimals."""
    number, code = fmt_money(amount, Currency.USDT).rsplit(" ", 1)

    assert (bool(_GROUPED_TWO.match(number)), code) == (True, "USDT")


@given(amount=_AMOUNTS)
def test_eur_money_has_the_symbol_in_front_and_two_decimals(amount: Decimal) -> None:
    """Any EUR amount renders with the euro sign first and exactly two decimals."""
    text = fmt_money(amount, Currency.EUR)

    assert bool(_GROUPED_TWO.match(text.replace("€", ""))) is True


@given(amount=_AMOUNTS)
def test_quantity_always_has_four_decimals_and_the_asset_code(amount: Decimal) -> None:
    """Any quantity renders with exactly four decimals followed by its asset code."""
    number, code = fmt_asset_qty(amount, Currency.BTC).rsplit(" ", 1)

    assert (bool(_GROUPED_FOUR.match(number)), code) == (True, "BTC")


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        (Decimal("61234.5"), Currency.USDT, "61,234.50 USDT"),
        (Decimal("1234.5"), Currency.EUR, "€1,234.50"),
        (Decimal("-0.004"), Currency.USDT, "0.00 USDT"),
        (Decimal("-1234.567"), Currency.USDT, "−1,234.57 USDT"),
        (Decimal("0.005"), Currency.EUR, "€0.01"),
    ],
)
def test_money_rounds_half_up_and_never_shows_negative_zero(
    amount: Decimal, currency: Currency, expected: str
) -> None:
    """Money rounds half up to cents; a value that rounds to zero carries no minus sign."""
    assert fmt_money(amount, currency) == expected


def test_quantity_keeps_trailing_zeros_to_four_places() -> None:
    """A quantity shows fixed precision, so 0.004 BTC reads 0.0040 BTC."""
    assert fmt_asset_qty(Decimal("0.004"), Currency.BTC) == "0.0040 BTC"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("-3.64"), "−3.6%"),
        (Decimal("1.25"), "+1.3%"),
        (Decimal("0"), "0.0%"),
        (Decimal("-0.04"), "0.0%"),
    ],
)
def test_distance_pct_is_signed_to_one_decimal(value: Decimal, expected: str) -> None:
    """A distance carries a plus or typographic minus sign and one decimal; zero is unsigned."""
    assert fmt_distance_pct(value) == expected


@given(value=st.decimals(min_value=Decimal(-1000), max_value=Decimal(1000), allow_nan=False))
def test_distance_pct_always_ends_with_one_decimal_percent(value: Decimal) -> None:
    """Any distance matches sign, digits, one decimal, percent sign."""
    assert re.sub(r"\d+", "N", fmt_distance_pct(value)) in {"N.N%", "+N.N%", "−N.N%"}


@pytest.mark.parametrize(
    ("ratio", "weight", "gap"),
    [
        (Decimal("0.35"), "35.0%", "35.0 pp"),
        (Decimal("0.05"), "5.0%", "5.0 pp"),
        (Decimal("0"), "0.0%", "0.0 pp"),
    ],
)
def test_weight_and_gap_are_percent_and_percentage_points(
    ratio: Decimal, weight: str, gap: str
) -> None:
    """A ratio shows as a percentage for a weight and as points for a gap, one decimal."""
    assert (fmt_weight(ratio), fmt_pp(ratio)) == (weight, gap)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-5, "0 s"),
        (0, "0 s"),
        (45, "45 s"),
        (60, "1 min"),
        (900, "15 min"),
        (3600, "1 h"),
        (7500, "2 h 5 min"),
        (86400, "1 d"),
        (97200, "1 d 3 h"),
    ],
)
def test_duration_reads_as_words(seconds: int, expected: str) -> None:
    """Durations show their two largest units; a negative span reads as zero."""
    assert fmt_duration(seconds) == expected


@given(seconds=st.integers(min_value=-10, max_value=10_000_000))
def test_duration_never_shows_raw_seconds_past_a_minute(seconds: int) -> None:
    """Any duration of a minute or more is written in min, h or d, never as bare seconds."""
    text = fmt_duration(seconds)

    assert (seconds < 60) == text.endswith(" s")


def test_winter_time_in_madrid_is_one_hour_ahead_of_utc() -> None:
    """In winter (CET) Madrid is UTC+1, so 12:00 UTC reads 13:00."""
    moment = datetime(2026, 1, 3, 12, 0, tzinfo=UTC)

    assert fmt_when(moment) == "Sat 13:00"


def test_summer_time_in_madrid_is_two_hours_ahead_of_utc() -> None:
    """In summer (CEST) Madrid is UTC+2, so 12:00 UTC reads 14:00."""
    moment = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    assert fmt_when(moment) == "Sat 14:00"


def test_dst_change_day_uses_the_offset_in_force() -> None:
    """On the autumn change (2026-10-25) 00:30 UTC is still CEST and 01:30 UTC is CET."""
    before = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    after = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)

    assert (fmt_when(before), fmt_when(after)) == ("Sun 02:30", "Sun 02:30")


def test_bar_close_just_before_the_hour_reads_as_the_hour() -> None:
    """A bar closing at 15:59:59.999 UTC reads as 16:00, not 15:59."""
    close = datetime(2026, 10, 2, 15, 59, 59, 999000, tzinfo=UTC)

    assert fmt_when_utc(close) == "Fri 18:00 (16:00 UTC)"


def test_verbose_time_adds_the_utc_clock_for_log_lookups() -> None:
    """The verbose form shows Madrid time and the UTC clock the VPS logs use."""
    moment = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    assert fmt_when_utc(moment) == "Sat 14:00 (12:00 UTC)"


@given(moment=st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2030, 1, 1)))
def test_time_is_always_weekday_and_hh_mm(moment: datetime) -> None:
    """Any instant formats as a weekday abbreviation and a 24-hour HH:MM."""
    text = fmt_when(moment.replace(tzinfo=UTC))

    assert (text[:3] in _WEEKDAYS, re.sub(r"\d", "9", text[3:])) == (True, " 99:99")


@given(cid=st.text(alphabet="0123456789ABCDEFGHJKMNPQRSTVWXYZ", min_size=0, max_size=40))
def test_ref_is_the_ellipsis_and_the_last_six_characters(cid: str) -> None:
    """The ref is always an ellipsis plus the last six characters, as ``hermes logs`` prints."""
    assert fmt_ref(cid) == "…" + cid[-6:]


@pytest.mark.parametrize(
    ("severity", "glyph"),
    [
        (AnomalySeverity.CRITICAL, GLYPH_STOP),
        (AnomalySeverity.WARNING, GLYPH_HALF),
        (AnomalySeverity.INFO, GLYPH_OK),
    ],
)
def test_severity_maps_to_one_glyph(severity: AnomalySeverity, glyph: str) -> None:
    """Critical is a stop, warning a half circle, info a filled circle."""
    assert severity_glyph(severity) == glyph


def test_quote_money_with_a_rate_is_the_amount_in_euros() -> None:
    """Invariant (668 R-1): 52,410.20 USDT at 1.25 USDT per euro reads EUR 41,928.16."""
    assert fmt_quote_money(Decimal("52410.2"), Currency.USDT, Decimal("1.25")) == "€41,928.16"


def test_quote_money_without_a_rate_keeps_the_quote_code() -> None:
    """Invariant (668 R-2): no rate, no invented euro figure."""
    assert fmt_quote_money(Decimal("52410.2"), Currency.USDT, None) == "52,410.20 USDT"


def test_quote_money_in_euros_ignores_the_rate() -> None:
    """Invariant: an amount already in EUR is never divided again."""
    assert fmt_quote_money(Decimal("100"), Currency.EUR, Decimal("1.25")) == "€100.00"


@given(
    amount=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=2),
    rate=st.decimals(min_value=Decimal("0.5"), max_value=Decimal("2"), places=4),
)
def test_quote_money_with_a_rate_is_always_a_euro_figure(amount: Decimal, rate: Decimal) -> None:
    """Invariant: with a rate the text starts with the euro sign and never names USDT."""
    text = fmt_quote_money(amount, Currency.USDT, rate)
    assert re.findall(r"€[\d,]+\.\d{2}", text) == [text]
