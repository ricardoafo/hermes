"""Shared Rich renderers for the operator console.

Each renderer turns view objects plus a :class:`RenderContext` into a Rich
renderable. The CLI prints them, the TUI embeds them and Telegram replies use
their plain text, so every surface shows the same fields. Renderers are pure:
``now``, the timezone, the width, the surface and the demo scenario all come
from the context.

Three rules hold in every renderer:

- money appears only when :func:`pnl_visible` allows it for the surface;
- a DEMO DATA tag heads the output whenever the data comes from a fixture;
- when hermes is down, money is dimmed and marked "as of" the stop time.

Reference: openspec/changes/console-read-model/change.md (D-2, D-4, D-5);
the approved console UX proposal (2026-10-03).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Console, Group, RenderableType
from rich.table import Table
from rich.text import Text

from hermes.application.console.format import (
    MINUS,
    TIMES,
    CushionLevel,
    arrow,
    big_digits,
    cushion_level,
    fmt_atr_range,
    fmt_change,
    fmt_eur,
    fmt_eur_price,
    fmt_level,
    fmt_pct,
    fmt_price,
    fmt_qty,
    fmt_stop,
    sparkline,
    stop_bar,
)
from hermes.application.console.read_model import ConsoleReadModel
from hermes.application.console.views import (
    ActivityDay,
    ActivityItem,
    ActivityKind,
    Heartbeat,
    HeartbeatState,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    ShadowTest,
    ShadowTestStatus,
    Tax,
    TaxEstimate,
    TaxPending,
)
from hermes.application.console.visibility import Surface, pnl_visible

__all__ = [
    "DEMO_TAG",
    "MADRID",
    "RenderContext",
    "framed",
    "render_activity",
    "render_heartbeat",
    "render_overview",
    "render_portfolio",
    "render_position_detail",
    "render_positions",
    "render_shadow_test",
    "render_tax_footnote",
    "to_plain_text",
]

MADRID: Final = ZoneInfo("Europe/Madrid")
DEMO_TAG: Final = "DEMO DATA"
_DEFAULT_DOWN_HINT: Final = "run hermes logs for why"
_WIDE_FROM: Final = 100
_SPARK_MAX: Final = 48
_OVERVIEW_ACTIVITY_ROWS: Final = 4
_HIDDEN_NOTE: Final = "money hidden on this surface"
_ZERO: Final = Decimal(0)

_RANGE_LABEL: Final[dict[PortfolioRange, str]] = {
    PortfolioRange.TODAY: "today",
    PortfolioRange.WEEK: "this week",
    PortfolioRange.MONTH: "this month",
    PortfolioRange.YEAR: "this year",
}
_CUSHION_STYLE: Final[dict[CushionLevel, str]] = {
    CushionLevel.GREEN: "green",
    CushionLevel.AMBER: "yellow",
    CushionLevel.RED: "red",
}


@dataclass(frozen=True, slots=True)
class RenderContext:
    """Everything a renderer needs besides the view objects.

    Attributes:
        surface: Where the output goes; decides whether money shows.
        now: The read time every relative time is measured from.
        tz: Display timezone (Europe/Madrid unless the operator asks for UTC).
        width: Target width in columns; 100 and over adds wide-only columns.
        demo_scenario: Fixture scenario name; set means DEMO DATA is shown.
        stale_since: When numbers froze because hermes is down, else ``None``.
        down_hint: Call to action on the down row (the TUI says "press L").
    """

    surface: Surface
    now: datetime
    tz: tzinfo = MADRID
    width: int = 80
    demo_scenario: str | None = None
    stale_since: datetime | None = None
    down_hint: str = _DEFAULT_DOWN_HINT

    @classmethod
    def for_model(
        cls,
        model: ConsoleReadModel,
        *,
        surface: Surface,
        tz: tzinfo = MADRID,
        width: int = 80,
        down_hint: str = _DEFAULT_DOWN_HINT,
    ) -> RenderContext:
        """Build a context whose clock, staleness and demo tag follow ``model``.

        Args:
            model: The read model the views come from.
            surface: Where the output goes.
            tz: Display timezone.
            width: Target width in columns.
            down_hint: Call to action on the down row.

        Returns:
            The context.
        """
        heartbeat = model.heartbeat()
        return cls(
            surface=surface,
            now=heartbeat.as_of,
            tz=tz,
            width=width,
            demo_scenario=model.demo_scenario,
            stale_since=heartbeat.stale_since,
            down_hint=down_hint,
        )

    @property
    def shows_money(self) -> bool:
        """Return whether money may appear on this surface."""
        return pnl_visible(self.surface)

    @property
    def wide(self) -> bool:
        """Return whether the width allows the wide-only columns."""
        return self.width >= _WIDE_FROM


def to_plain_text(renderable: RenderableType, *, width: int) -> str:
    """Render to plain text with no colour codes and no trailing spaces.

    Used for snapshots, non-TTY output and Telegram replies.

    Args:
        renderable: What to render.
        width: Console width in columns.

    Returns:
        The text, one line per row, ending with a newline.
    """
    console = Console(
        width=width,
        file=io.StringIO(),
        record=True,
        color_system=None,
        force_terminal=False,
        legacy_windows=False,
        emoji=False,
        markup=False,
        highlight=False,
        _environ={},
    )
    console.print(renderable)
    lines = console.export_text(styles=False).splitlines()
    return "".join(f"{line.rstrip()}\n" for line in lines)


# ----------------------------------------------------------------------
# Shared pieces
# ----------------------------------------------------------------------


def _framed(ctx: RenderContext, parts: Sequence[RenderableType]) -> Group:
    if ctx.demo_scenario is None:
        return Group(*parts)
    tag = Text(f" {DEMO_TAG} · scenario {ctx.demo_scenario} ", style="bold black on yellow")
    return Group(tag, *parts)


def framed(ctx: RenderContext, parts: Sequence[RenderableType]) -> Group:
    """Stack ``parts``, headed by the DEMO DATA tag when the data is a fixture.

    Panels in their own modules use this so every screen carries the same tag.

    Args:
        ctx: Render context; its demo scenario decides the tag.
        parts: The panel's renderables, top to bottom.

    Returns:
        The stacked group.
    """
    return _framed(ctx, parts)


def _clock(ctx: RenderContext, at: datetime) -> str:
    return at.astimezone(ctx.tz).strftime("%H:%M")


def _stamp(ctx: RenderContext, at: datetime) -> str:
    return at.astimezone(ctx.tz).strftime("%a %d %b %H:%M")


def _duration(span: timedelta) -> str:
    minutes = max(int(span.total_seconds() // 60), 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def _money_style(ctx: RenderContext, value: Decimal) -> str:
    if ctx.stale_since is not None:
        return "dim"
    direction = arrow(value)
    if direction == "▲":
        return "green"
    if direction == "▼":
        return "red"
    return ""


def _money(ctx: RenderContext, value: Decimal, suffix: str = "") -> Text:
    return Text(fmt_eur(value) + suffix, style=_money_style(ctx, value))


def _stale_note(ctx: RenderContext) -> str:
    if ctx.stale_since is None:
        return ""
    return f" · as of {_clock(ctx, ctx.stale_since)}"


def _grid(*, wide_padding: bool = False) -> Table:
    return Table(
        box=None,
        show_header=False,
        pad_edge=False,
        padding=(0, 2 if wide_padding else 1),
    )


# ----------------------------------------------------------------------
# Heartbeat
# ----------------------------------------------------------------------


def render_heartbeat(heartbeat: Heartbeat, ctx: RenderContext) -> Group:
    """Render the status line: quiet when OK, amber when late, a red row when down.

    Args:
        heartbeat: The heartbeat view.
        ctx: Render context.

    Returns:
        One right-aligned line (OK, late) or one full-width row (down).
    """
    if heartbeat.state is HeartbeatState.DOWN and heartbeat.down_since is not None:
        line = Text(
            f"✖ hermes down since {_clock(ctx, heartbeat.down_since)} · {ctx.down_hint}",
            style="bold red",
        )
    elif heartbeat.state is HeartbeatState.LATE:
        late_by = _duration(heartbeat.as_of - heartbeat.next_check_at)
        line = Text(
            f"◐ {_clock(ctx, heartbeat.next_check_at)} check late · {late_by}",
            style="yellow",
            justify="right",
        )
    else:
        line = Text(
            f"● live · checked {_clock(ctx, heartbeat.last_check_at)}"
            f" · next {_clock(ctx, heartbeat.next_check_at)}",
            style="dim",
            justify="right",
        )
    return _framed(ctx, [line])


# ----------------------------------------------------------------------
# Portfolio hero and waterfall
# ----------------------------------------------------------------------


def _tax_cell(ctx: RenderContext, tax: Tax) -> Text:
    if isinstance(tax, TaxPending):
        return Text("n/a", style="dim")
    suffix = " credit" if tax.amount < _ZERO else ""
    return _money(ctx, -tax.amount, suffix)


def render_tax_footnote(summary: PortfolioSummary) -> Text:
    """Render the tax footnote the ``?`` help screen shows, not the home screen.

    Args:
        summary: The portfolio view carrying the tax band.

    Returns:
        ``est. · FIFO · 19% band · €X left before 21%``, or the pending label
        while the tax module has not landed.
    """
    columns = (summary.today, summary.year)
    band = summary.tax_band
    if band is None or any(isinstance(c.tax, TaxPending) for c in columns):
        return Text(f"{TaxPending().label} · net is before tax", style="dim")
    parts = ["est.", band.method, f"{band.rate:.0f}% band"]
    if band.next_rate is not None and band.room_to_next is not None:
        parts.append(f"{fmt_level(band.room_to_next)} left before {band.next_rate:.0f}%")
    return Text(" · ".join(parts), style="dim")


def _open_line(ctx: RenderContext, summary: PortfolioSummary) -> Text:
    line = Text("Open ")
    line.append_text(_money(ctx, summary.open_pnl))
    if isinstance(summary.open_tax_if_sold, TaxEstimate):
        line.append(" · tax if sold now ")
        line.append_text(_tax_cell(ctx, summary.open_tax_if_sold))
    else:
        line.append(f" · {summary.open_tax_if_sold.label}", style="dim")
    return line


def _waterfall(ctx: RenderContext, summary: PortfolioSummary) -> Table:
    table = Table(box=None, pad_edge=False, padding=(0, 2), show_edge=False)
    table.add_column("")
    table.add_column("Today", justify="right")
    table.add_column("This year", justify="right")
    today, year = summary.today, summary.year
    table.add_row("Gross", _money(ctx, today.gross), _money(ctx, year.gross))
    table.add_row(f"{MINUS} Fees", _money(ctx, -today.fees), _money(ctx, -year.fees))
    table.add_row(f"{MINUS} Est. tax", _tax_cell(ctx, today.tax), _tax_cell(ctx, year.tax))
    table.add_row(
        Text("Net", style="bold"),
        _money(ctx, today.net),
        _money(ctx, year.net),
    )
    return table


def _share(part: Decimal, whole: Decimal) -> str:
    if whole == _ZERO:
        return "0.00%"
    return f"{(part * 100 / whole):.2f}%"


def render_portfolio(
    summary: PortfolioSummary, ctx: RenderContext, *, big_total: bool = False
) -> Group:
    """Render the hero (total value, change, net) and the waterfall.

    The waterfall covers closed trades only; open positions get their own line.
    The tax footnote is not part of it (see :func:`render_tax_footnote`).
    Blind surfaces get a single line with no figures.

    Args:
        summary: The portfolio view for the chosen range.
        ctx: Render context.
        big_total: Draw the total value three rows tall (the TUI on tall
            terminals), with the change on the line below.

    Returns:
        The portfolio block.
    """
    if not ctx.shows_money:
        return _framed(ctx, [Text(f"Portfolio · {_HIDDEN_NOTE}", style="dim")])
    label = _RANGE_LABEL[summary.range]
    total_style = "dim" if ctx.stale_since else "bold"
    hero = Text()
    if not big_total:
        hero.append(fmt_level(summary.total_value), style=total_style)
        hero.append("   ")
    hero.append(
        f"{fmt_change(summary.change, summary.change_pct)} {label}",
        style=_money_style(ctx, summary.change),
    )
    hero.append(_stale_note(ctx), style="dim")
    net_line = Text("net ")
    net_line.append_text(_money(ctx, summary.net, f" {label}"))
    if isinstance(summary.year.tax, TaxPending):
        net_line.append(" · before tax", style="dim")
    spark = sparkline(summary.sparkline, min(_SPARK_MAX, ctx.width - 2))
    cash_line = Text(
        f"cash {fmt_level(summary.cash)} {_share(summary.cash, summary.total_value)}"
        f" · invested {fmt_level(summary.invested)}"
        f" {_share(summary.invested, summary.total_value)}",
        style="dim",
    )
    parts: list[RenderableType] = [
        *(
            [Text(row, style=total_style) for row in big_digits(fmt_level(summary.total_value))]
            if big_total
            else []
        ),
        hero,
        net_line,
        Text(spark, style="dim" if ctx.stale_since else "cyan"),
        cash_line,
    ]
    if summary.starting_capital is not None and summary.started_on is not None:
        started = summary.started_on
        parts.append(
            Text(
                f"started {fmt_level(summary.starting_capital)} on {started.day} {started:%b}",
                style="dim",
            )
        )
    parts += [Text(""), _waterfall(ctx, summary), _open_line(ctx, summary)]
    return _framed(ctx, parts)


# ----------------------------------------------------------------------
# Positions
# ----------------------------------------------------------------------


def _eur_caption() -> Text:
    return Text("prices in EUR", style="dim")


def _blind_positions(rows: Sequence[PositionRow], ctx: RenderContext) -> Table:
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, show_edge=False)
    for name in ("coin", "side", "opened", "entry", "size", "stop"):
        table.add_column(name)
    for row in rows:
        table.add_row(
            row.coin,
            row.side,
            _stamp(ctx, row.opened_at),
            fmt_price(row.entry_eur),
            fmt_qty(row.size),
            fmt_price(row.stop_eur),
        )
    return table


def _cushion_style(ctx: RenderContext, row: PositionRow) -> str:
    if ctx.stale_since is not None:
        return "dim"
    return _CUSHION_STYLE[cushion_level(row.stop_distance_atr)]


def _stop_cell(ctx: RenderContext, row: PositionRow) -> Text:
    return Text(fmt_stop(row.stop_eur, row.stop_distance_pct), style=_cushion_style(ctx, row))


def _stop_bar_cell(ctx: RenderContext, row: PositionRow) -> Text:
    cell = Text(
        stop_bar(row.stop_price, row.current_price, row.high_since_entry),
        style=_cushion_style(ctx, row),
    )
    cell.append(f" {fmt_atr_range(row.stop_distance_atr)}")
    return cell


def render_positions(
    rows: Sequence[PositionRow], ctx: RenderContext, *, selected: str | None = None
) -> Group:
    """Render one row per open position; money is net only, prices are EUR.

    The stop column is the stop price and the distance to it in percent,
    green above 2 ATR, amber from 1 to 2 and red below 1 (the ATR itself is
    only named in the detail). Blind surfaces show coin, side, opened at,
    entry, size and stop level only.

    Args:
        rows: Open positions.
        ctx: Render context.
        selected: Coin to mark with a leading arrow (the TUI cursor).

    Returns:
        The positions table, or the empty-state line.
    """
    if not rows:
        return _framed(ctx, [Text("No open shadow positions.", style="dim")])
    if not ctx.shows_money:
        return _framed(ctx, [_blind_positions(rows, ctx), _eur_caption()])
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, show_edge=False)
    table.add_column("coin")
    table.add_column("tf")
    table.add_column("entry → now")
    table.add_column("change", justify="right")
    table.add_column("net" + _stale_note(ctx), justify="right")
    table.add_column("stop")
    for row in rows:
        marker = "" if selected is None else ("▸" if row.coin == selected else " ")
        cells: list[RenderableType] = [
            Text(f"{marker}{row.coin}", style="bold"),
            row.timeframe,
            f"{fmt_eur_price(row.entry_eur)} → {fmt_eur_price(row.current_eur)}",
            Text(fmt_pct(row.change_pct), style=_money_style(ctx, row.change_pct)),
            _money(ctx, row.net, " net"),
            _stop_cell(ctx, row),
        ]
        table.add_row(*cells)
    return _framed(ctx, [table])


# ----------------------------------------------------------------------
# Position detail
# ----------------------------------------------------------------------


def _detail_money(ctx: RenderContext, detail: PositionDetail) -> Table:
    table = _grid(wide_padding=True)
    table.add_column("")
    table.add_column("", justify="right")
    table.add_row("Gross", _money(ctx, detail.gross))
    table.add_row(f"{MINUS} Fees paid", _money(ctx, -detail.fees_paid))
    table.add_row(f"{MINUS} Fees to close (est.)", _money(ctx, -detail.fees_to_close))
    if isinstance(detail.tax, TaxPending):
        table.add_row(f"{MINUS} Est. tax if sold now", Text(detail.tax.label, style="dim"))
    else:
        table.add_row(f"{MINUS} Est. tax if sold now", _tax_cell(ctx, detail.tax))
    table.add_row(Text("Net", style="bold"), _money(ctx, detail.net))
    return table


def _timeline(ctx: RenderContext, detail: PositionDetail) -> Table:
    table = _grid(wide_padding=True)
    table.add_column("", style="dim")
    table.add_column("")
    for event in detail.timeline:
        table.add_row(_stamp(ctx, event.at), event.label)
    return table


def render_position_detail(detail: PositionDetail | None, ctx: RenderContext) -> Group:
    """Render one position: header, why it opened, stop, money, chart, timeline.

    Args:
        detail: The position, or ``None`` when no position matches.
        ctx: Render context.

    Returns:
        The detail block.
    """
    if detail is None:
        return _framed(ctx, [Text("No open position for that coin.", style="dim")])
    row = detail.row
    age = _duration(ctx.now - row.opened_at)
    header = Text()
    header.append(row.coin, style="bold")
    header.append(f" · {row.timeframe} · {row.side} · open {age}")
    if not ctx.shows_money:
        facts = Text(
            f"opened {_stamp(ctx, row.opened_at)} · entry {fmt_price(row.entry_eur)}"
            f" · size {fmt_qty(row.size)} · stop {fmt_price(row.stop_eur)}"
        )
        return _framed(ctx, [header, facts, _eur_caption()])
    header.append("   ")
    header.append_text(_money(ctx, detail.net, " net"))
    header.append(_stale_note(ctx), style="dim")
    size_line = Text(
        f"size {row.size:.4f} {row.coin} · now worth {fmt_level(detail.now_worth)}"
        f" · cost {fmt_level(detail.cost)}"
    )
    values = " · ".join(f"{name} {fmt_eur_price(value)}" for name, value in detail.entry_values)
    why = Text(f"Why: {detail.strategy} on {row.timeframe} · entry {fmt_eur_price(row.entry_eur)}")
    if values:
        why.append(f" · {values}")
    stop = Text("Stop: ")
    stop.append_text(_stop_bar_cell(ctx, row))
    stop.append(
        f" · {fmt_stop(row.stop_eur, row.stop_distance_pct)}"
        f" · high since entry {fmt_eur_price(row.high_eur)}"
        f" · moved {detail.stop_moves}{TIMES}"
    )
    chart = Text("Price ")
    chart.append(sparkline(detail.price_path, min(_SPARK_MAX, ctx.width - 40)), style="cyan")
    chart.append(
        f" entry {fmt_eur_price(row.entry_eur)} · stop {fmt_eur_price(row.stop_eur)}"
        f" · high {fmt_eur_price(row.high_eur)}",
        style="dim",
    )
    quoted = Text(
        f"quoted in USDT · entry {fmt_price(row.entry_price)} · now {fmt_price(row.current_price)}"
        f" · stop {fmt_price(row.stop_price)} · high {fmt_price(row.high_since_entry)}",
        style="dim",
    )
    parts: list[RenderableType] = [
        header,
        size_line,
        why,
        stop,
        Text(""),
        Text("Money if sold now", style="bold"),
        _detail_money(ctx, detail),
        Text(""),
        chart,
        quoted,
        Text(""),
        Text("Timeline", style="bold"),
        _timeline(ctx, detail),
    ]
    return _framed(ctx, parts)


# ----------------------------------------------------------------------
# Activity
# ----------------------------------------------------------------------


def _day_header(ctx: RenderContext, day: ActivityDay) -> str:
    today = ctx.now.astimezone(ctx.tz).date()
    if day.day == today:
        return "Today"
    if day.day == today - timedelta(days=1):
        return "Yesterday"
    return day.day.strftime("%a %d %b")


def _when(ctx: RenderContext, item: ActivityItem, *, is_today: bool) -> str:
    if not is_today:
        return _clock(ctx, item.at)
    minutes = int((ctx.now - item.at).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:  # noqa: PLR2004
        return f"{minutes}m ago"
    return f"{minutes // 60}h ago"


def _price_text(ctx: RenderContext, value: Decimal) -> str:
    return fmt_eur_price(value) if ctx.shows_money else fmt_price(value)


def _closed_text(ctx: RenderContext, item: ActivityItem, coin: str, price: str) -> Text:
    if not ctx.shows_money:
        return Text(f"closed {coin}")
    text = Text(f"closed {coin}{price}")
    if item.net is not None:
        text.append(" · ")
        text.append_text(_money(ctx, item.net, " net"))
    return text


def _stop_raised_text(ctx: RenderContext, item: ActivityItem, coin: str) -> Text:
    times = f" {TIMES}{item.count}" if item.count > 1 else ""
    levels = ""
    if item.stop_from is not None and item.stop_to is not None:
        rate = item.eur_per_usdt
        levels = (
            f", {_price_text(ctx, item.stop_from * rate)}→{_price_text(ctx, item.stop_to * rate)}"
        )
    return Text(f"{coin} stop raised{times}{levels}")


def _activity_text(ctx: RenderContext, item: ActivityItem) -> Text:
    coin = item.coin or ""
    price_eur = item.price_eur
    price = f" at {_price_text(ctx, price_eur)}" if price_eur is not None else ""
    note = item.text or ""
    match item.kind:
        case ActivityKind.OPENED:
            text = Text(f"opened {coin}{price}")
        case ActivityKind.CLOSED:
            text = _closed_text(ctx, item, coin, price)
        case ActivityKind.STOP_RAISED:
            text = _stop_raised_text(ctx, item, coin)
        case ActivityKind.RESTART:
            text = Text(f"restart · {note}" if note else "restart", style="dim")
        case ActivityKind.WARNING:
            text = Text(f"⚠ {note}", style="yellow")
        case ActivityKind.ERROR:
            text = Text(f"✖ {note}", style="red")
        case ActivityKind.BAR | ActivityKind.SIGNAL:
            text = Text(f"{item.kind.value} {coin} {note}".rstrip(), style="dim")
    return text


def render_activity(
    days: Sequence[ActivityDay], ctx: RenderContext, *, limit: int | None = None
) -> Group:
    """Render the day-grouped activity feed, newest first.

    Args:
        days: Activity grouped by local day, newest day first.
        ctx: Render context.
        limit: Maximum number of rows across all days (the home screen shows 3-4).

    Returns:
        The feed, or the empty-state line.
    """
    parts: list[RenderableType] = []
    remaining = limit
    for day in days:
        if remaining is not None and remaining <= 0:
            break
        items = day.items if remaining is None else day.items[:remaining]
        if not items:
            continue
        header = _day_header(ctx, day)
        parts.append(Text(header, style="bold"))
        table = _grid(wide_padding=True)
        table.add_column("", style="dim", min_width=8)
        table.add_column("")
        for item in items:
            table.add_row(_when(ctx, item, is_today=header == "Today"), _activity_text(ctx, item))
        parts.append(table)
        if remaining is not None:
            remaining -= len(items)
    if not parts:
        parts.append(Text("No activity yet.", style="dim"))
    return _framed(ctx, parts)


# ----------------------------------------------------------------------
# Shadow test and overview
# ----------------------------------------------------------------------


def render_shadow_test(test: ShadowTest, ctx: RenderContext) -> Group:
    """Render the one-line 90-day shadow test status.

    Args:
        test: The shadow test view.
        ctx: Render context.

    Returns:
        For example ``Shadow test · day 12 of 90 · on track``.
    """
    if test.status is ShadowTestStatus.NOT_STARTED or test.day is None:
        line = "Shadow test · not started"
    else:
        line = f"Shadow test · day {test.day} of {test.length_days} · {test.status.value}"
    if test.note:
        line += f" · {test.note}"
    return _framed(ctx, [Text(line, style="dim")])


def render_overview(model: ConsoleReadModel, ctx: RenderContext) -> Group:
    """Render every section stacked, as on an 80x24 terminal.

    Args:
        model: The read model.
        ctx: Render context (one DEMO DATA tag heads the whole overview).

    Returns:
        Heartbeat, portfolio for today, positions, the latest activity rows
        and the shadow test line.
    """
    inner = replace(ctx, demo_scenario=None)
    parts: list[RenderableType] = [
        render_heartbeat(model.heartbeat(), inner),
        render_portfolio(model.portfolio(PortfolioRange.TODAY), inner),
        Text(""),
        render_positions(model.positions(), inner),
        Text(""),
        render_activity(model.activity(), inner, limit=_OVERVIEW_ACTIVITY_ROWS),
        Text(""),
        render_shadow_test(model.shadow_test(), inner),
    ]
    return _framed(ctx, parts)
