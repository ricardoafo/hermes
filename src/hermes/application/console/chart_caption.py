"""What the Portfolio chart covers, in words and on a time axis (issue #713).

The Code Owner found the curve hard to read (2026-10-06): a flat row of blocks
said nothing about the period, how much history it holds or that flat means
"no change". :func:`chart_caption` builds the dim line under every Portfolio
chart (the compact sparkline and the braille curve) and :func:`time_axis` the
marks under the braille curve. Both are pure, so the console and the TUI share
them and tests check the text directly.

Money rule: the start, end and high values are the summary's EUR samples (the
rate rule of :mod:`hermes.application.console.portfolio_figures`) and only show
when the caller says the surface may show money (``pnl_visible``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from hermes.application.console.format import MINUS, round2
from hermes.application.console.views import PortfolioRange, PortfolioSummary

__all__ = [
    "RANGE_LETTERS",
    "RANGE_WORDS",
    "axis_span",
    "chart_caption",
    "is_flat",
    "resample_by_time",
    "time_axis",
]

RANGE_WORDS: Final[dict[PortfolioRange, str]] = {
    PortfolioRange.TODAY: "today",
    PortfolioRange.WEEK: "this week",
    PortfolioRange.MONTH: "this month",
    PortfolioRange.YEAR: "this year",
}
RANGE_LETTERS: Final[dict[PortfolioRange, str]] = {
    PortfolioRange.TODAY: "D",
    PortfolioRange.WEEK: "W",
    PortfolioRange.MONTH: "M",
    PortfolioRange.YEAR: "Y",
}
_SEP: Final = " · "
_FLAT: Final = "flat"
_ARROW: Final = " → "
_WHOLE: Final = Decimal(1)
_MIN_MARKS: Final = 3
_MOVING_FROM: Final = 2
_MARK_GAP: Final = 1
_HOUR: Final = timedelta(hours=1)
_WEEKDAYS: Final = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS: Final = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


def is_flat(values: Sequence[Decimal]) -> bool:
    """Return whether a series does not move at cent precision.

    Args:
        values: The samples, oldest first.

    Returns:
        ``True`` for fewer than two samples or when the lowest and highest
        round to the same cent.
    """
    return len(values) < _MOVING_FROM or round2(min(values)) == round2(max(values))


def _whole_eur(value: Decimal) -> str:
    rounded = value.quantize(_WHOLE, rounding=ROUND_HALF_UP)
    prefix = MINUS if rounded < 0 else ""
    return f"{prefix}€{abs(rounded):,}"


def _since(summary: PortfolioSummary, tz: tzinfo) -> str | None:
    """Return ``3 Oct`` (``09:00`` for today) when the history starts inside the range."""
    times, start = summary.sparkline_at, summary.range_from
    if not times or start is None or times[0] <= start:
        return None
    local = times[0].astimezone(tz)
    if summary.range is PortfolioRange.TODAY:
        return local.strftime("%H:%M")
    return f"{local.day} {_MONTHS[local.month - 1]}"


def chart_caption(
    summary: PortfolioSummary,
    *,
    tz: tzinfo,
    width: int,
    compact: bool,
    show_values: bool,
) -> str:
    """Return the dim line under a Portfolio chart: period, history, movement.

    Wide: ``this week · 18 checks since 3 Oct · flat``, or with movement
    ``this year · 2,190 checks · €4,454 → €5,932 · high €6,612``. Compact:
    ``W · 18 checks · flat`` (no "since"). When the line is too long the high
    goes first, then the values, then "since"; a line that still does not fit
    is cut at ``width``.

    Args:
        summary: The portfolio view; its ``sparkline`` is EUR already.
        tz: Display timezone of the "since" date.
        width: Most cells the line may take.
        compact: Use the range letter and leave out "since".
        show_values: The surface may show money; without it a moving series
            gets no values (a flat one still says "flat").

    Returns:
        The caption, or empty text with no samples or no width.
    """
    values = summary.sparkline
    if not values or width < 1:
        return ""
    label = (RANGE_LETTERS if compact else RANGE_WORDS)[summary.range]
    checks = f"{len(values):,} check{'' if len(values) == 1 else 's'}"
    since = None if compact else _since(summary, tz)
    tail: list[str] = []
    if is_flat(values):
        tail.append(_FLAT)
    elif show_values:
        tail.append(f"{_whole_eur(values[0])}{_ARROW}{_whole_eur(values[-1])}")
        tail.append(f"high {_whole_eur(max(values))}")
    while True:
        head = f"{checks} since {since}" if since else checks
        line = _SEP.join([label, head, *tail])
        if len(line) <= width:
            return line
        if tail and tail != [_FLAT]:
            tail.pop()
        elif since is not None:
            since = None
        else:
            return line[:width]


def axis_span(summary: PortfolioSummary) -> tuple[datetime, datetime] | None:
    """Return the period the curve and its axis cover: first sample (or range start) to read time.

    Args:
        summary: The portfolio view.

    Returns:
        ``(start, end)``, or ``None`` when neither the sample times nor the
        range start are known, or the period is empty.
    """
    start = summary.sparkline_at[0] if summary.sparkline_at else summary.range_from
    if start is None or start >= summary.as_of:
        return None
    return start, summary.as_of


def resample_by_time(
    values: Sequence[Decimal],
    times: Sequence[datetime],
    span: tuple[datetime, datetime],
    points: int,
) -> list[Decimal]:
    """Return ``points`` values evenly spaced in time over ``span``.

    Each point takes the sample in force at its instant (the last one at or
    before it; the first sample before the first time), so the drawn curve
    has a time scale its axis can label.

    Args:
        values: The samples, oldest first.
        times: When each sample took effect, oldest first.
        span: ``(start, end)`` of the curve.
        points: How many points to return.

    Returns:
        The resampled series; empty for no samples or no points.
    """
    if not values or points < 1:
        return []
    start, end = span
    step = (end - start) / max(points - 1, 1)
    out: list[Decimal] = []
    index = 0
    for point in range(points):
        at = start + step * point
        while index + 1 < len(times) and times[index + 1] <= at:
            index += 1
        out.append(values[index])
    return out


_Instants = Callable[[datetime, datetime, tzinfo], Iterator[datetime]]


@dataclass(frozen=True, slots=True)
class _Ticks:
    """One way to mark an axis: the instants and how each is labelled."""

    instants: _Instants
    label: Callable[[datetime], str]


def _hours(every: int) -> _Instants:
    def instants(start: datetime, end: datetime, tz: tzinfo) -> Iterator[datetime]:
        at = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if at < start:
            at += _HOUR
        while at <= end:
            if at.astimezone(tz).hour % every == 0:
                yield at
            at += _HOUR

    return instants


def _midnights(start: datetime, end: datetime, tz: tzinfo) -> Iterator[date]:
    day = start.astimezone(tz).date()
    while day <= end.astimezone(tz).date():
        at = datetime.combine(day, time(), tzinfo=tz)
        if start <= at <= end:
            yield day
        day += timedelta(days=1)


def _days(keep: Callable[[date], bool]) -> _Instants:
    def instants(start: datetime, end: datetime, tz: tzinfo) -> Iterator[datetime]:
        for day in _midnights(start, end, tz):
            if keep(day):
                yield datetime.combine(day, time(), tzinfo=tz)

    return instants


def _hour_label(tz: tzinfo) -> Callable[[datetime], str]:
    return lambda at: f"{at.astimezone(tz).hour:02d}"


def _weekday_label(tz: tzinfo) -> Callable[[datetime], str]:
    return lambda at: _WEEKDAYS[at.astimezone(tz).weekday()]


def _day_label(tz: tzinfo) -> Callable[[datetime], str]:
    return lambda at: str(at.astimezone(tz).day)


def _month_label(tz: tzinfo) -> Callable[[datetime], str]:
    return lambda at: _MONTHS[at.astimezone(tz).month - 1]


def _month_day_every(days: int) -> Callable[[date], bool]:
    return lambda day: (day.day - 1) % days == 0


def _ordinal_every(days: int) -> Callable[[date], bool]:
    return lambda day: day.toordinal() % days == 0


def _month_start_every(months: int) -> Callable[[date], bool]:
    return lambda day: day.day == 1 and (day.month - 1) % months == 0


def _ladder(range_: PortfolioRange, tz: tzinfo) -> list[_Ticks]:
    """Return the ways to mark a range, preferred first (coarser next, finer last)."""
    hours = {n: _Ticks(_hours(n), _hour_label(tz)) for n in (1, 3, 6, 12)}
    month_days = [_Ticks(_days(_month_day_every(n)), _day_label(tz)) for n in (7, 14)]
    every_day = _Ticks(_days(_month_day_every(1)), _day_label(tz))
    match range_:
        case PortfolioRange.TODAY:
            return [hours[6], hours[12], hours[3], hours[1]]
        case PortfolioRange.WEEK:
            weekdays = [_Ticks(_days(_ordinal_every(n)), _weekday_label(tz)) for n in (1, 2)]
            return [*weekdays, hours[6], hours[3]]
        case PortfolioRange.MONTH:
            return [*month_days, every_day, hours[6]]
        case PortfolioRange.YEAR:
            months = [_Ticks(_days(_month_start_every(n)), _month_label(tz)) for n in (1, 2, 3, 6)]
            return [*months, *month_days, every_day]


def _place(
    marks: Sequence[tuple[datetime, str]], span: tuple[datetime, datetime], width: int
) -> str | None:
    """Return the marks laid out on one line, or ``None`` when two would touch."""
    start, end = span
    total = (end - start).total_seconds()
    line = [" "] * width
    used_to = -_MARK_GAP
    for at, label in marks:
        if len(label) > width:
            return None
        column = round((at - start).total_seconds() / total * (width - 1))
        column = min(column, width - len(label))
        if column < used_to + _MARK_GAP:
            return None
        line[column : column + len(label)] = label
        used_to = column + len(label)
    return "".join(line).rstrip()


def time_axis(
    range_: PortfolioRange, span: tuple[datetime, datetime], *, tz: tzinfo, width: int
) -> str | None:
    """Return the dim time marks under the braille curve, fitted to ``width``.

    Hours for today (``00 06 12 18``), weekdays for the week, day numbers for
    the month, month names for the year; a coarser step when the preferred
    one does not fit, a finer one when the history is too short for three marks.

    Args:
        range_: The portfolio range.
        span: ``(start, end)`` the curve covers (:func:`axis_span`).
        tz: Display timezone.
        width: Cells of the curve.

    Returns:
        The axis line, or ``None`` when no step places at least three marks
        without two touching.
    """
    start, end = span
    if width < 1 or end <= start:
        return None
    for ticks in _ladder(range_, tz):
        marks = [(at, ticks.label(at)) for at in ticks.instants(start, end, tz)]
        if len(marks) < _MIN_MARKS:
            continue
        line = _place(marks, span, width)
        if line is not None:
            return line
    return None
