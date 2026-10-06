"""The Portfolio chart's caption and time axis (issue #713, Code Owner 2026-10-06).

Synthetic data only: every summary below is built in the test.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console import PortfolioRange, PortfolioSummary
from hermes.application.console.chart_caption import (
    axis_span,
    chart_caption,
    is_flat,
    resample_by_time,
    time_axis,
)
from hermes.application.console.views import TaxPending, WaterfallColumn

_AS_OF = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_MADRID = ZoneInfo("Europe/Madrid")
_WEEK_START = _AS_OF - timedelta(days=7)


def _summary(
    values: list[str],
    *,
    range_: PortfolioRange = PortfolioRange.WEEK,
    times: tuple[datetime, ...] = (),
    range_from: datetime | None = _WEEK_START,
) -> PortfolioSummary:
    column = WaterfallColumn(gross=Decimal(0), fees=Decimal(0), tax=TaxPending())
    return PortfolioSummary(
        range=range_,
        as_of=_AS_OF,
        total_value=Decimal(values[-1]) if values else Decimal(0),
        change=Decimal(0),
        change_pct=Decimal(0),
        net=Decimal(0),
        sparkline=tuple(Decimal(v) for v in values),
        cash=Decimal(0),
        invested=Decimal(0),
        today=column,
        year=column,
        open_pnl=Decimal(0),
        open_tax_if_sold=TaxPending(),
        tax_band=None,
        sparkline_at=times,
        range_from=range_from,
    )


def _caption(
    summary: PortfolioSummary, *, width: int = 80, compact: bool = False, money: bool = True
) -> str:
    return chart_caption(summary, tz=_MADRID, width=width, compact=compact, show_values=money)


def _every_4h(count: int, first: datetime) -> tuple[datetime, ...]:
    return tuple(first + timedelta(hours=4) * i for i in range(count))


@pytest.mark.parametrize(
    ("range_", "words"),
    [
        (PortfolioRange.TODAY, "today"),
        (PortfolioRange.WEEK, "this week"),
        (PortfolioRange.MONTH, "this month"),
        (PortfolioRange.YEAR, "this year"),
    ],
)
def test_caption_names_the_range_in_words(range_: PortfolioRange, words: str) -> None:
    """Invariant: the wide caption opens with the range's words."""
    summary = _summary(["5000", "5000"], range_=range_, range_from=None)
    assert _caption(summary) == f"{words} · 2 checks · flat"


def test_short_history_says_how_many_checks_and_since_when() -> None:
    """Invariant: a history starting inside the range names its first day."""
    first = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    summary = _summary(["5000"] * 18, times=_every_4h(18, first))
    assert _caption(summary) == "this week · 18 checks since 3 Oct · flat"


def test_full_history_gives_the_count_without_since() -> None:
    """Invariant: a history covering the whole range has no "since"."""
    summary = _summary(["5000"] * 18, times=(_WEEK_START, *_every_4h(17, _WEEK_START)[1:]))
    assert _caption(summary) == "this week · 18 checks · flat"


def test_short_history_today_names_the_clock_time() -> None:
    """Invariant: on the today range "since" is a local clock time, not a date."""
    midnight = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
    first = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
    summary = _summary(
        ["5000", "5000"],
        range_=PortfolioRange.TODAY,
        times=(first, first + timedelta(hours=4)),
        range_from=midnight,
    )
    assert _caption(summary) == "today · 2 checks since 09:00 · flat"


def test_moving_series_gives_start_end_and_high_in_whole_euros() -> None:
    """Invariant: a moving series shows start → end and the range high, EUR rounded."""
    values = ["4454.20", *["6000"] * 2187, "6612.49", "5931.50"]
    summary = _summary(values, range_=PortfolioRange.YEAR, range_from=None)
    assert _caption(summary) == "this year · 2,190 checks · €4,454 → €5,932 · high €6,612"


def test_moving_series_without_money_has_no_values() -> None:
    """Invariant: where the surface may not show money the caption carries no figure."""
    summary = _summary(["4454", "6612", "5932"], range_=PortfolioRange.YEAR, range_from=None)
    assert _caption(summary, money=False) == "this year · 3 checks"


def test_flat_series_says_flat_even_without_money() -> None:
    """Invariant: "flat" is no money figure, so it shows on every surface."""
    summary = _summary(["5000", "5000", "5000"], range_from=None)
    assert _caption(summary, money=False) == "this week · 3 checks · flat"


def test_sub_cent_wiggle_is_flat() -> None:
    """Invariant: a series that does not move at cent precision is flat."""
    assert (is_flat([Decimal("5000.001"), Decimal("5000.004")]), is_flat([Decimal(1)])) == (
        True,
        True,
    )


def test_one_cent_move_is_not_flat() -> None:
    """Invariant: a one-cent move is movement."""
    assert is_flat([Decimal("5000.00"), Decimal("5000.01")]) is False


def test_compact_uses_the_range_letter_and_no_since() -> None:
    """Invariant: the compact form is ``W · 18 checks · flat``."""
    first = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    summary = _summary(["5000"] * 18, times=_every_4h(18, first))
    assert _caption(summary, compact=True) == "W · 18 checks · flat"


def test_compact_moving_keeps_the_values() -> None:
    """Invariant: the compact form keeps start → end and the high when they fit."""
    summary = _summary(["4454", "6612", "5932"], range_=PortfolioRange.YEAR, range_from=None)
    assert _caption(summary, compact=True) == "Y · 3 checks · €4,454 → €5,932 · high €6,612"


def test_narrow_width_drops_the_high_then_the_values_then_since() -> None:
    """Invariant: the caption never runs past its width; detail goes in a fixed order."""
    first = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    summary = _summary(["4454", "6612", "5932"], times=_every_4h(3, first))
    full = "this week · 3 checks since 3 Oct · €4,454 → €5,932 · high €6,612"
    assert [_caption(summary, width=w) for w in (len(full), 50, 33, 20, 8)] == [
        full,
        "this week · 3 checks since 3 Oct · €4,454 → €5,932",
        "this week · 3 checks since 3 Oct",
        "this week · 3 checks",
        "this wee",
    ]


def test_no_samples_no_caption() -> None:
    """Invariant: an empty series has no caption."""
    assert _caption(_summary([])) == ""


@given(
    values=st.lists(
        st.decimals(min_value=0, max_value=10**7, places=2, allow_nan=False), min_size=1
    ),
    width=st.integers(min_value=1, max_value=120),
    compact=st.booleans(),
)
def test_caption_never_exceeds_its_width(values: list[Decimal], width: int, compact: bool) -> None:
    """Invariant: for any series and width the caption fits in ``width`` cells."""
    summary = replace(_summary(["1"]), sparkline=tuple(values))
    assert len(_caption(summary, width=width, compact=compact)) <= width


def test_today_axis_marks_six_hourly() -> None:
    """Invariant: today's axis is ``00 06 12 18`` placed by time."""
    start = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    span = (start, start + timedelta(hours=23))
    assert time_axis(PortfolioRange.TODAY, span, tz=UTC, width=24) == "00    06    12    18"


def test_week_axis_marks_weekdays() -> None:
    """Invariant: the week's axis names each day starting inside the span."""
    axis = time_axis(PortfolioRange.WEEK, (_WEEK_START, _AS_OF), tz=UTC, width=60)
    assert axis is not None
    assert axis.split() == ["Wed", "Thu", "Fri", "Sat", "Sun", "Mon", "Tue"]


def test_month_axis_marks_day_numbers() -> None:
    """Invariant: the month's axis is day numbers a week apart (1, 8, 15, 22, 29)."""
    span = (_AS_OF - timedelta(days=30), _AS_OF)
    axis = time_axis(PortfolioRange.MONTH, span, tz=UTC, width=60)
    assert axis is not None
    assert axis.split() == ["8", "15", "22", "29", "1"]


def test_year_axis_marks_month_names() -> None:
    """Invariant: the year's axis names each month from January."""
    span = (datetime(2026, 1, 1, tzinfo=UTC), _AS_OF)
    axis = time_axis(PortfolioRange.YEAR, span, tz=UTC, width=60)
    assert axis is not None
    assert axis.split() == [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
    ]  # fmt: skip


def test_year_axis_thins_to_every_other_month_when_narrow() -> None:
    """Invariant: when every month does not fit, a coarser step is used."""
    span = (datetime(2026, 1, 1, tzinfo=UTC), _AS_OF)
    axis = time_axis(PortfolioRange.YEAR, span, tz=UTC, width=30)
    assert axis is not None
    assert axis.split() == ["Jan", "Mar", "May", "Jul", "Sep"]


def test_axis_is_left_out_when_three_marks_do_not_fit() -> None:
    """Invariant: a panel too narrow for three marks gets no axis; one cell wider gets one.

    Over 23 hours the 12-hourly step has only two marks and the finer steps
    touch below 12 cells, so 11 cells draw nothing and 12 draw ``00 06 12 18``.
    """
    start = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    span = (start, start + timedelta(hours=23))
    assert [time_axis(PortfolioRange.TODAY, span, tz=UTC, width=w) for w in (11, 12)] == [
        None,
        "00 06 12 18",
    ]


def test_short_history_uses_a_finer_step() -> None:
    """Invariant: a week with two days of history marks hours instead of weekdays."""
    span = (_AS_OF - timedelta(hours=40), _AS_OF)
    axis = time_axis(PortfolioRange.WEEK, span, tz=UTC, width=60)
    assert axis is not None
    assert axis.split() == ["00", "06", "12", "18", "00", "06", "12"]


def test_axis_span_starts_at_the_first_sample_else_the_range() -> None:
    """Invariant: the curve covers first sample (or range start) to read time."""
    first = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    with_times = _summary(["1", "2"], times=(first, first + timedelta(hours=4)))
    without = _summary(["1", "2"])
    assert (axis_span(with_times), axis_span(without)) == (
        (first, _AS_OF),
        (_WEEK_START, _AS_OF),
    )


def test_resample_takes_the_value_in_force_at_each_instant() -> None:
    """Invariant: each point is the last sample at or before its time."""
    start = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    times = (start, start + timedelta(hours=6))
    values = [Decimal(1), Decimal(2)]
    span = (start, start + timedelta(hours=8))
    assert resample_by_time(values, times, span, 5) == [
        Decimal(1),
        Decimal(1),
        Decimal(1),
        Decimal(2),
        Decimal(2),
    ]
