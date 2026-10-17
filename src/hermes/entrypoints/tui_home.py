"""What the Cockpit home screen shows at each size (design spec Home row, issue #712).

:func:`home_layout` turns a terminal size into the breakpoint decisions
(columns, curve, sparklines, big total, log tail); the other functions build
the cockpit content of each panel from read-model views. Every function that
draws money takes the :class:`~hermes.application.console.render.RenderContext`
and draws no figure when the surface may not show P&L (``pnl_visible``). The
colours come from the data token sheet: up blue, down orange, warning amber.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from rich.console import Group, RenderableType
from rich.text import Text

from hermes.application.console import (
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    ShadowTest,
    ShadowTestStatus,
)
from hermes.application.console.chart_caption import (
    axis_span,
    chart_caption,
    resample_by_time,
    time_axis,
)
from hermes.application.console.format import (
    MINUS,
    CushionLevel,
    cushion_level,
    fmt_eur,
    fmt_eur_price,
    fmt_level,
    fmt_pct,
    fmt_price,
    fmt_usdt_price,
    round2,
    stop_room_share,
)
from hermes.application.console.live import LivePosition
from hermes.application.console.logs import LogLine, render_log_line
from hermes.application.console.render import (
    RenderContext,
    render_positions,
    shadow_test_line,
)
from hermes.entrypoints.tui_charts import area_chart, spark_text
from hermes.entrypoints.tui_live import LiveLook
from hermes.entrypoints.tui_theme import (
    COMPACT_BELOW,
    CURVE_ROWS_STANDARD,
    CURVE_ROWS_WIDE,
    LEFT_COLUMN_STANDARD,
    LEFT_COLUMN_WIDE,
    LOG_TAIL_ROWS,
    METER_WARN_SHARE,
    RADAR_FRAMES,
    TALL_FROM,
    TOWERING_CHART_FACTOR,
    TOWERING_FROM,
    ULTRA_FROM,
    WIDE_FROM,
    Tokens,
)
from hermes.entrypoints.tui_widgets import Meter

__all__ = [
    "LOGS_DEMO",
    "LOGS_UNAVAILABLE",
    "HeightClass",
    "HomeLayout",
    "LiveRows",
    "WidthClass",
    "counted_line",
    "empty_positions",
    "exposure_share",
    "home_layout",
    "log_tail",
    "portfolio_curve",
    "portfolio_meters",
    "position_rows",
    "run_bar",
    "run_line",
    "total_text",
]

LOGS_DEMO: Final = "Logs come from the VPS journal; demo data has none."
LOGS_UNAVAILABLE: Final = "The journal is not readable here; run hermes logs on the VPS."
_NO_LOGS: Final = "No journal lines yet."
_GUTTER: Final = 1
# The home body keeps a one-cell vertical scrollbar gutter at every width.
_SCROLLBAR: Final = 1
_MAX_METER: Final = 32
_LABEL: Final = 9
_FIGURE: Final = 11
_SHARE: Final = 7
_MIN_METER: Final = 6
_POSITION_SPARK: Final = 10
_STOP_METER: Final = 8
_STOP_METER_NARROW: Final = 6
_PRICES_FROM: Final = 92
_TREND_FROM: Final = 64
_RUN_TAIL: Final = 6
_MIN_RUN_BAR: Final = 10
_HUNDRED: Final = Decimal(100)


class WidthClass(StrEnum):
    """Breakpoint token of the terminal width."""

    COMPACT = "compact"
    STANDARD = "standard"
    WIDE = "wide"
    ULTRA = "ultra"


class HeightClass(StrEnum):
    """Breakpoint token of the terminal height."""

    SHORT = "short"
    TALL = "tall"
    TOWERING = "towering"


@dataclass(frozen=True, slots=True)
class HomeLayout:
    """Every breakpoint decision of the home screen for one terminal size.

    Attributes:
        width_class: compact < 100, standard 100-159, wide 160-219, ultra >= 220.
        height_class: short < 30, tall 30-44, towering >= 45.
        columns: 1, 2 or 3 columns of panels.
        left: Width of the Portfolio column in cells.
        right: Width of the Positions column in cells.
        third: Width of the Signals / log-tail column (ultra only, else 0); the
            Positions and third columns share the rest 3:2.
        curve_rows: Rows of the braille curve; 0 hides it.
        sparklines: Charts are sparkline lines (compact).
        big_total: The total is the big number (not compact, not short);
            otherwise the head's plain bold total line.
        market_beside_activity: Market and Activity share a row (the mock layout).
        log_rows: Lines of the log tail (ultra only, else 0).
        activity_rows: Activity rows shown.
    """

    width_class: WidthClass
    height_class: HeightClass
    columns: int
    left: int
    right: int
    third: int
    curve_rows: int
    sparklines: bool
    big_total: bool
    market_beside_activity: bool
    log_rows: int
    activity_rows: int

    @property
    def classes(self) -> tuple[str, str]:
        """Return the CSS classes of the layout, for example ``-wide`` and ``-towering``."""
        return f"-{self.width_class.value}", f"-{self.height_class.value}"


def _width_class(width: int) -> WidthClass:
    if width < COMPACT_BELOW:
        return WidthClass.COMPACT
    if width < WIDE_FROM:
        return WidthClass.STANDARD
    if width < ULTRA_FROM:
        return WidthClass.WIDE
    return WidthClass.ULTRA


def _height_class(height: int) -> HeightClass:
    if height < TALL_FROM:
        return HeightClass.SHORT
    if height < TOWERING_FROM:
        return HeightClass.TALL
    return HeightClass.TOWERING


_ACTIVITY_ROWS: Final[dict[HeightClass, int]] = {
    HeightClass.SHORT: 3,
    HeightClass.TALL: 4,
    HeightClass.TOWERING: 8,
}


def home_layout(width: int, height: int) -> HomeLayout:
    """Return the home layout for a terminal of ``width`` x ``height`` cells.

    Args:
        width: Terminal columns.
        height: Terminal rows.

    Returns:
        One column under 100 columns (charts as sparklines), two from 100, the
        mock layout from 160 (Market beside Activity), three from 220. Under 30
        rows the curve is hidden; the big total shows only from 100 columns and
        30 rows; from 45 rows the curve is
        twice as tall.
    """
    width_class = _width_class(width)
    height_class = _height_class(height)
    factor = TOWERING_CHART_FACTOR if height_class is HeightClass.TOWERING else 1
    compact = width_class is WidthClass.COMPACT
    usable = width - _SCROLLBAR
    if compact:
        left = right = usable
        third = 0
        curve = 0
    else:
        left = LEFT_COLUMN_STANDARD if width_class is WidthClass.STANDARD else LEFT_COLUMN_WIDE
        rest = usable - left - _GUTTER
        if width_class is WidthClass.ULTRA:
            third = (rest - _GUTTER) * 2 // 5
            right = rest - _GUTTER - third
        else:
            right, third = rest, 0
        base = CURVE_ROWS_STANDARD if width_class is WidthClass.STANDARD else CURVE_ROWS_WIDE
        curve = 0 if height_class is HeightClass.SHORT else base * factor
    ultra = width_class is WidthClass.ULTRA
    return HomeLayout(
        width_class=width_class,
        height_class=height_class,
        columns=3 if ultra else 1 if compact else 2,
        left=left,
        right=right,
        third=third,
        curve_rows=curve,
        sparklines=compact,
        big_total=not compact and height_class is not HeightClass.SHORT,
        market_beside_activity=width_class in (WidthClass.WIDE, WidthClass.ULTRA),
        log_rows=LOG_TAIL_ROWS * factor if ultra else 0,
        activity_rows=_ACTIVITY_ROWS[height_class],
    )


def _shows_figures(summary: PortfolioSummary, ctx: RenderContext) -> bool:
    return ctx.shows_money and not summary.eur_pending


def _direction(tokens: Tokens, value: Decimal, ctx: RenderContext) -> str:
    """Return the data colour of a change: up blue, down orange, dim when stale or flat."""
    rounded = round2(value)
    if ctx.stale_since is not None or rounded == 0:
        return tokens.dim
    return tokens.up if rounded > 0 else tokens.down


def total_text(summary: PortfolioSummary, ctx: RenderContext) -> str | None:
    """Return the big total's figure, or ``None`` when it may not or cannot show.

    Args:
        summary: The portfolio view.
        ctx: Render context.

    Returns:
        ``€1,021.06``; ``None`` on a blind surface or while EUR is pending.
    """
    return fmt_level(summary.total_value) if _shows_figures(summary, ctx) else None


def portfolio_curve(
    summary: PortfolioSummary, ctx: RenderContext, tokens: Tokens, *, width: int, rows: int
) -> Text:
    """Return the braille area curve of the range's total value, its time axis and caption.

    The gain gradient when the range ends at or above its start, the loss
    gradient below; one dim colour while hermes is down. With sample times the
    curve has a time scale (each dot column is the value in force at its
    instant) and a dim axis of hours, weekdays, day numbers or months under it
    (left out when three marks do not fit); the dim caption says what the chart
    covers (issue #713, Code Owner 2026-10-06).

    Args:
        summary: The portfolio view.
        ctx: Render context.
        tokens: Data token sheet.
        width: Cells per row.
        rows: Rows; 0 draws nothing.

    Returns:
        The curve, axis and caption, or empty text on a blind surface, while
        EUR is pending, or with no samples.
    """
    values = summary.sparkline
    if rows < 1 or width < 1 or not values or not _shows_figures(summary, ctx):
        return Text("")
    if ctx.stale_since is not None:
        stops: tuple[str, ...] = (tokens.dim, tokens.dim)
    else:
        stops = tokens.gain if values[-1] >= values[0] else tokens.loss
    span = axis_span(summary)
    drawn: Sequence[Decimal] = values
    if span is not None and len(summary.sparkline_at) == len(values):
        drawn = resample_by_time(values, summary.sparkline_at, span, width * 2)
    lines = [area_chart([float(v) for v in drawn], width=width, rows=rows, stops=stops)]
    axis = None if span is None else time_axis(summary.range, span, tz=ctx.tz, width=width)
    if axis:
        lines.append(Text(axis, style=tokens.dim))
    caption = chart_caption(
        summary, tz=ctx.tz, width=width, compact=False, show_values=ctx.shows_money
    )
    lines.append(Text(caption, style=tokens.dim))
    return Text("\n").join(lines)


def exposure_share(summary: PortfolioSummary) -> Decimal | None:
    """Return invested EUR as a share of the start capital (else of the total value).

    Args:
        summary: The portfolio view.

    Returns:
        A share such as ``Decimal("0.2259")``, or ``None`` with no base to divide by.
    """
    base = summary.starting_capital
    if base is None or base <= 0:
        base = summary.total_value
    if base <= 0:
        return None
    return summary.invested / base


def _pct(share: Decimal) -> str:
    return f"{share * _HUNDRED:.1f}%"


def _meter_line(label: str, bar: Text, tail: str) -> Text:
    line = Text(f"{label:<{_LABEL}}", style="")
    line.append_text(bar)
    line.append(tail)
    return line


def portfolio_meters(
    summary: PortfolioSummary,
    ctx: RenderContext,
    tokens: Tokens,
    *,
    width: int,
    cap: Decimal | None,
    cash: bool = True,
) -> Group:
    """Return the cash, invested and exposure meters.

    The exposure meter fills its share of the roster's cap and turns amber
    from 80% of it; without a cap it says the cap is not known.

    Args:
        summary: The portfolio view.
        ctx: Render context.
        tokens: Data token sheet.
        width: Cells available.
        cap: The pre-registered exposure cap as a share (``0.15``), or ``None``.
        cash: Draw the cash and invested meters (the compact layout prints
            them as a text line instead).

    Returns:
        The meters; nothing on a blind surface or while EUR is pending.
    """
    if not _shows_figures(summary, ctx):
        return Group()
    cells = min(_MAX_METER, max(_MIN_METER, width - _LABEL - _FIGURE - _SHARE))
    total = summary.total_value
    lines: list[RenderableType] = []
    if cash:
        for label, value, colour in (
            ("cash", summary.cash, tokens.live),
            ("invested", summary.invested, tokens.up),
        ):
            share = value / total if total > 0 else Decimal(0)
            tail = f" {fmt_level(value):>{_FIGURE - 1}} {_pct(share):>{_SHARE - 1}}"
            bar = Meter(float(share), width=cells).render(tokens, colour=colour)
            lines.append(_meter_line(label, bar, tail))
    exposure = exposure_share(summary)
    if exposure is None:
        return Group(*lines)
    if cap is None or cap <= 0:
        lines.append(
            _meter_line(
                "exposure",
                Text(_pct(exposure), style=tokens.muted),
                " · cap unknown",
            )
        )
        return Group(*lines)
    tail = f" {_pct(exposure)} of {_pct(cap).replace('.0%', '%')} cap"
    cells = min(cells, max(_MIN_METER, width - _LABEL - len(tail)))
    exposure_meter = Meter(float(exposure / cap), width=cells, warn_from=METER_WARN_SHARE)
    line = _meter_line("exposure", exposure_meter.render(tokens), "")
    line.append(tail, style=tokens.warn if exposure_meter.warned else "")
    lines.append(line)
    return Group(*lines)


@dataclass(frozen=True, slots=True)
class _RowColumns:
    """Which position-row columns fit a width."""

    prices: bool
    trend: int
    meter: int
    stop_word: bool

    @classmethod
    def for_width(cls, width: int) -> _RowColumns:
        if width >= _PRICES_FROM:
            return cls(prices=True, trend=_POSITION_SPARK, meter=_STOP_METER, stop_word=True)
        if width >= _TREND_FROM:
            return cls(prices=False, trend=_POSITION_SPARK, meter=_STOP_METER, stop_word=False)
        return cls(prices=False, trend=0, meter=_STOP_METER_NARROW, stop_word=False)


def _stop_room(
    row: PositionRow,
    ctx: RenderContext,
    tokens: Tokens,
    columns: _RowColumns,
    *,
    live: LivePosition | None = None,
    look: LiveLook | None = None,
) -> Text:
    """Return the stop-room meter and its words; amber when the stop is within 2 ATR.

    With a live position the meter and the distance are measured from the live price.
    """
    if live is None:
        share = float(stop_room_share(row.stop_price, row.current_price, row.high_since_entry))
        atr, pct = row.stop_distance_atr, row.stop_distance_pct
    else:
        share = float(live.stop_room)
        atr = row.stop_distance_atr if live.stop_distance_atr is None else live.stop_distance_atr
        pct = live.stop_distance_pct
    close = cushion_level(atr) is not CushionLevel.GREEN
    paused = live is not None and look is not None and look.paused
    dimmed = ctx.stale_since is not None or paused
    bar = Meter(share, width=columns.meter, warn=close).render(
        tokens, colour=tokens.dim if dimmed else None
    )
    word = " stop" if columns.stop_word else ""
    distance = f"{word} {MINUS}{abs(pct):.1f}%"
    style = tokens.warn if close else tokens.muted
    bar.append(distance, style=tokens.dim if paused else style)
    return bar


def _header(columns: _RowColumns, ctx: RenderContext, tokens: Tokens) -> Text:
    stale = (
        "" if ctx.stale_since is None else f" · as of {ctx.stale_since.astimezone(ctx.tz):%H:%M}"
    )
    header = Text(f" {'coin':<6}{'tf':<4}", style=tokens.muted)
    if columns.prices:
        header.append(f"{'entry → now':<24}")
    if columns.trend:
        header.append(f"{'trend':<{columns.trend + 1}}")
    header.append(f"{'change':>9} {'net' + stale:>12}  stop room")
    return header


def _tagged(header: Text, look: LiveLook | None, width: int) -> list[RenderableType]:
    """Return the header with the live tag at its end, or the tag on its own line above."""
    if look is None:
        return [header]
    tag = look.tag()
    if header.cell_len + 2 + tag.cell_len <= width:
        header.append("  ")
        header.append_text(tag)
        return [header]
    return [tag, header]


@dataclass(frozen=True, slots=True)
class LiveRows:
    """The live layer of the position rows (issue #713).

    Attributes:
        look: The live tag, the flash and the paused state.
        positions: Each coin's position valued at its live price.
        ticks: Each coin's kept live prices, oldest first, appended to its trend.
    """

    look: LiveLook
    positions: Mapping[str, LivePosition]
    ticks: Mapping[str, Sequence[Decimal]]


def _move(row: PositionRow, live: LivePosition | None) -> tuple[str, str]:
    """Return the entry and the now price, the now price live when there is one."""
    if row.eur_pending:
        now = row.current_price if live is None else live.price
        return fmt_price(row.entry_price), fmt_usdt_price(now)
    now_eur = row.current_eur if live is None or live.price_eur is None else live.price_eur
    return fmt_eur_price(row.entry_eur), fmt_eur_price(now_eur)


def _row(
    row: PositionRow,
    detail: PositionDetail | None,
    ctx: RenderContext,
    *,
    tokens: Tokens,
    columns: _RowColumns,
    selected: str | None,
    live: LiveRows | None = None,
) -> Text:
    position = None if live is None else live.positions.get(row.coin)
    look = None if live is None or position is None else live.look
    line = Text("▸" if row.coin == selected else " ", style=f"bold {tokens.live}")
    line.append(f"{row.coin:<6}", style="bold")
    line.append(f"{row.timeframe:<4}")
    change = row.change_pct if position is None else position.change_pct
    colour = _direction(tokens, change, ctx)
    if look is not None:
        colour = look.style(colour)
    if columns.prices:
        entry, now = _move(row, position)
        line.append(f"{entry} → ")
        line.append(now, style="" if look is None else look.flash(row.coin, ""))
        line.append(" " * max(0, 24 - len(entry) - 3 - len(now)))
    if columns.trend:
        path = list(detail.price_path) if detail is not None else []
        if live is not None and position is not None:
            path += live.ticks.get(row.coin, ())
        spark = spark_text(path, columns.trend, colour)
        line.append_text(spark)
        line.append(" " * (columns.trend + 1 - spark.cell_len))
    change_style = colour if look is None else look.flash(row.coin, colour)
    line.append(f"{fmt_pct(change):>9}", style=change_style)
    net = row.net if position is None else position.net
    if row.eur_pending:
        line.append(f" {'EUR pending':>12}", style=tokens.dim)
    elif net is None:
        line.append(f" {'…':>12}", style=tokens.dim)
    else:
        net_colour = _direction(tokens, net, ctx)
        line.append(
            f" {fmt_eur(net):>12}", style=net_colour if look is None else look.style(net_colour)
        )
    line.append("  ")
    line.append_text(_stop_room(row, ctx, tokens, columns, live=position, look=look))
    return line


def _booked_line(rows: Sequence[PositionRow], look: LiveLook, tokens: Tokens) -> Text:
    """Return ``booked as of 16:00 · net ETH ▲ +€1.54 · SOL ▲ +€6.05`` (net at the last bar)."""
    line = look.booked()
    line.append(" · net ", style=tokens.muted)
    for index, row in enumerate(rows):
        if index:
            line.append(" · ", style=tokens.dim)
        figure = "EUR pending" if row.eur_pending else fmt_eur(row.net)
        line.append(f"{row.coin} {figure}", style=tokens.muted)
    return line


def position_rows(
    rows: Sequence[PositionRow],
    details: Mapping[str, PositionDetail | None],
    ctx: RenderContext,
    tokens: Tokens,
    *,
    width: int,
    selected: str | None = None,
    live: LiveRows | None = None,
) -> Group:
    """Return one cockpit row per open position: trend, change, net, stop room.

    The stop-room meter is the share of room left from the stop up to the high
    since entry; it is amber when the stop is within 2 ATR. Blind surfaces get
    the shared positions table without money.

    With a live layer, a position with a live price shows its now price,
    change, net (before tax), stop room and trend at that price; the header
    carries the live tag and a last line keeps the booked net per coin ("booked
    as of 16:00"). Paused, the live cells dim; a tick flashes the now price and
    the change.

    Args:
        rows: Open positions.
        details: Each coin's detail (for its price path), when the model has it.
        ctx: Render context.
        tokens: Data token sheet.
        width: Cells available: from 92 the entry and current prices show, from
            64 the trend sparkline.
        selected: Coin marked with ``▸`` (the cursor).
        live: The live layer, or ``None`` to draw the booked rows only.

    Returns:
        A header line and the rows (and the booked line when live).
    """
    if not ctx.shows_money:
        return render_positions(rows, ctx, selected=selected)
    columns = _RowColumns.for_width(width)
    showing = live is not None and live.look.showing and bool(live.positions)
    look = live.look if live is not None and showing else None
    lines = _tagged(_header(columns, ctx, tokens), look, width)
    lines += [
        _row(
            row,
            details.get(row.coin),
            ctx,
            tokens=tokens,
            columns=columns,
            selected=selected,
            live=live if showing else None,
        )
        for row in rows
    ]
    if look is not None:
        lines.append(_booked_line(rows, look, tokens))
    return Group(*lines)


def empty_positions(
    coins: Sequence[str], next_at: datetime | None, ctx: RenderContext, tokens: Tokens, frame: int
) -> Text:
    """Return the designed empty state: a radar and what hermes is watching.

    Args:
        coins: Coins hermes checks (from the market watch).
        next_at: When it looks again, or ``None`` when unknown.
        ctx: Render context (its timezone).
        tokens: Data token sheet.
        frame: Radar step; the same step draws the same frame.

    Returns:
        ``⠁ No open positions`` and ``hermes is watching BTC ETH SOL and will
        look again at 18:00``.
    """
    out = Text(RADAR_FRAMES[frame % len(RADAR_FRAMES)], style=f"bold {tokens.live}")
    out.append(" No open positions", style="bold")
    if next_at is None:
        return out
    watched = " ".join(coins) if coins else "the market"
    out.append("\n\nhermes is watching ", style=tokens.muted)
    out.append(watched, style="bold")
    out.append(" and will look again at ", style=tokens.muted)
    out.append(f"{next_at.astimezone(ctx.tz):%H:%M}", style=f"bold {tokens.live}")
    out.append(
        "\nA position appears here when a rule fires. Nothing is traded from this screen.",
        style=tokens.dim,
    )
    return out


def log_tail(
    lines: Sequence[LogLine] | None, ctx: RenderContext, *, demo: bool, count: int
) -> RenderableType:
    """Return the last ``count`` journal lines, or why there are none.

    Args:
        lines: The journal lines read, or ``None`` when no journal is readable here.
        ctx: Render context (its timezone).
        demo: The data is a demo scenario, which has no journal.
        count: Most lines to show.

    Returns:
        The lines as ``hermes logs`` prints them, or one dim sentence.
    """
    if demo:
        return Text(LOGS_DEMO, style="dim")
    if lines is None:
        return Text(LOGS_UNAVAILABLE, style="dim")
    if not lines:
        return Text(_NO_LOGS, style="dim")
    return Group(*(render_log_line(line, ctx.tz) for line in list(lines)[-count:]))


def run_bar(day: int, length_days: int, tokens: Tokens, *, width: int) -> Text:
    """Return the shared 90-day run bar: the brand-gradient :class:`Meter` of ``day``.

    The bottom run line and the Shadow test hero (issue #718) both draw it, so
    they can never disagree on how far the run is.

    Args:
        day: The run's day.
        length_days: The run's length.
        tokens: Token sheet.
        width: Cells.

    Returns:
        ``width`` block cells, filled ``round(day / length_days x width)``.
    """
    return Meter(day, cap=length_days, width=width).render(tokens, stops=tokens.brand)


def run_line(test: ShadowTest, tokens: Tokens, *, width: int) -> Text:
    """Return the 90-day line of a :class:`ShadowTest` view (the seed-based count).

    Args:
        test: The shadow test view.
        tokens: Token sheet.
        width: Cells available.

    Returns:
        See :func:`counted_line`.
    """
    day = None if test.status is ShadowTestStatus.NOT_STARTED else test.day
    return counted_line(shadow_test_line(test), day, test.length_days, tokens, width=width)


def counted_line(
    words: str, day: int | None, length_days: int, tokens: Tokens, *, width: int
) -> Text:
    """Return the 90-day line: words, the shared run bar and the percentage.

    Args:
        words: The line's words.
        day: The counted day, ``None`` when nothing counts (no bar).
        length_days: The run's length.
        tokens: Token sheet.
        width: Cells available.

    Returns:
        ``Shadow test · day 12 of 90  █████░░░ 13%``; the words only when no
        day counts or there is no room for a bar.
    """
    out = Text(words, style="dim")
    if day is None or length_days <= 0:
        return out
    cells = width - len(words) - 2 - _RUN_TAIL
    if cells < _MIN_RUN_BAR:
        return out
    out.append("  ")
    out.append_text(run_bar(day, length_days, tokens, width=cells))
    share = round(min(max(day, 0), length_days) * 100 / length_days)
    out.append(f" {share:>3}%", style=tokens.muted)
    return out
