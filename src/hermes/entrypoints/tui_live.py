"""The live layer of the Cockpit console: tag, flash and Market rows (issue #713).

Live values (the 10 s ticker, :mod:`hermes.application.console.live`) are drawn
apart from booked values (the ledger at the last bar close):

- :class:`LiveLook` carries what every live cell needs: the state (waiting,
  live, paused), the pulsing dot colour, the cells flashing after a tick and
  the time of the booked values. Paused (no tick for more than 60 s) dims
  every live value; a tick flashes the changed cell blue (up) or orange (down).
- :func:`market_rows` draws the Market panel: per coin the live price, its
  change over the kept ticks, a braille sparkline and the next check. A coin
  whose last fetch failed keeps its last price, dimmed, with "as of HH:MM:SS".

Display only: nothing here is written anywhere, and no engine, supervisor or
notification code imports this module (import-linter contract
``live-prices-are-console-only``).

Reference: openspec/archive/713-live-ticker/change.md.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from rich.console import Group, RenderableType
from rich.text import Text

from hermes.application.console import PortfolioSummary, PositionRow, WatchedPair
from hermes.application.console.format import (
    fmt_change,
    fmt_eur,
    fmt_eur_price,
    fmt_level,
    fmt_pct,
    fmt_price,
    fmt_usdt_price,
)
from hermes.application.console.live import (
    LIVE_COINS,
    LivePosition,
    LiveQuote,
    LiveState,
    LiveStatus,
    LiveTotal,
)
from hermes.application.console.live_book import BookHealth
from hermes.application.console.live_context import BarContext, DayContext, FormingBar
from hermes.application.console.render import RenderContext, day_clock, range_label
from hermes.entrypoints.tui_charts import braille_spark
from hermes.entrypoints.tui_theme import LIVE_GLYPH, Tokens

__all__ = [
    "LiveLook",
    "book_line",
    "context_lines",
    "eur_rate",
    "latency_chip",
    "live_detail_line",
    "live_portfolio_lines",
    "live_tag",
    "market_rows",
]

_WAITING_GLYPH: Final = "○"
_COIN: Final = 5
_TF: Final = 4
_PRICE: Final = 11
_CHANGE: Final = 10
_RESULT: Final = 7
_SPREAD: Final = 8
_NEXT: Final = 9
_FULL_FROM: Final = 64
_MIN_CHART: Final = 4
_MAX_CHART: Final = 24
_WINDOW: Final = "15m"
_NONE: Final = "…"
_CONTEXT_INDENT: Final = " " * _COIN
_DIVIDER: Final = Text(" │ ")
_VOLUME_WHOLE_FROM: Final = Decimal(100)
_LATENCY_SPARK: Final = 8
_THOUSAND: Final = Decimal(1000)
_MILLION: Final = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class LiveLook:
    """How the live cells look right now.

    Attributes:
        status: Waiting, live or paused, and the newest good tick.
        tokens: Data token sheet (up, down, dim, live).
        dot: The pulsing dot's colour at this pulse step.
        ctx: Render context (timezone, read day).
        flashes: Coins whose cells flash now: ``1`` up, ``-1`` down. The app
            fills it only with motion on.
        booked_at: When the booked values were booked (the last bar close).
    """

    status: LiveStatus
    tokens: Tokens
    dot: str
    ctx: RenderContext
    flashes: Mapping[str, int] = field(default_factory=dict)
    booked_at: datetime | None = None

    @property
    def showing(self) -> bool:
        """Return whether there is a live price to show (not still waiting)."""
        return self.status.state is not LiveState.WAITING

    @property
    def paused(self) -> bool:
        """Return whether the ticker has been stale for more than 60 s."""
        return self.status.state is LiveState.PAUSED

    def style(self, colour: str) -> str:
        """Return ``colour``, or the dim token while paused."""
        return self.tokens.dim if self.paused else colour

    def flash(self, coin: str, colour: str) -> str:
        """Return the cell style of ``coin``: the flash while it flashes, else :meth:`style`.

        Args:
            coin: The coin the cell belongs to.
            colour: The cell's resting colour.

        Returns:
            ``bold <void> on <up>`` (or ``<down>``) while flashing, else the colour.
        """
        direction = self.flashes.get(coin)
        if direction is None or self.paused:
            return self.style(colour)
        background = self.tokens.up if direction > 0 else self.tokens.down
        return f"bold {self.tokens.void} on {background}"

    def tag(self) -> Text:
        """Return the live tag: ``● live`` (pulsing), ``● live · paused 14:02:10`` or waiting."""
        return live_tag(self.status, self.dot, self.tokens, self.ctx)

    def booked(self) -> Text:
        """Return the booked layer's label, ``booked as of 16:00``."""
        when = (
            ""
            if self.booked_at is None
            else f" as of {self.booked_at.astimezone(self.ctx.tz):%H:%M}"
        )
        return Text(f"booked{when}", style=self.tokens.muted)


def live_tag(status: LiveStatus, dot: str, tokens: Tokens, ctx: RenderContext) -> Text:
    """Return the tag every live value carries.

    Args:
        status: The live layer's state.
        dot: The pulsing dot's colour (the live dot's pulse step).
        tokens: Data token sheet.
        ctx: Render context (its timezone).

    Returns:
        ``● live`` with the pulsing dot; ``● live · paused 14:02:10`` (amber dot,
        dim words) when stale; ``○ live · waiting for prices`` before the first tick.
    """
    if status.state is LiveState.WAITING or status.last_tick_at is None:
        return Text(f"{_WAITING_GLYPH} live · waiting for prices", style=tokens.dim)
    out = Text()
    if status.state is LiveState.PAUSED:
        out.append(f"{LIVE_GLYPH} ", style=f"bold {tokens.warn}")
        at = status.last_tick_at.astimezone(ctx.tz)
        out.append(f"live · paused {at:%H:%M:%S}", style=tokens.dim)
        return out
    out.append(f"{LIVE_GLYPH} ", style=f"bold {dot}")
    out.append("live", style=f"bold {tokens.live}")
    return out


def eur_rate(rows: Sequence[PositionRow], summary: PortfolioSummary | None) -> Decimal | None:
    """Return the booked EUR rate for one quote unit, to show live prices in EUR.

    Args:
        rows: The open positions; the first priced row's rate wins.
        summary: The portfolio view; its total over its quote total is the fallback.

    Returns:
        EUR per USDT, or ``None`` when no booked rate is known (prices show in USDT).
    """
    for row in rows:
        if not row.eur_pending:
            return row.eur_per_usdt
    if summary is None or summary.eur_pending or not summary.total_value_quote:
        return None
    return summary.total_value / summary.total_value_quote


def _price(price: Decimal, rate: Decimal | None) -> str:
    return fmt_eur_price(price * rate) if rate is not None else fmt_price(price)


@dataclass(frozen=True, slots=True)
class _Columns:
    full: bool
    chart: int

    @classmethod
    def for_width(cls, width: int) -> _Columns:
        full = width >= _FULL_FROM
        fixed = _COIN + _PRICE + _CHANGE + 1 + _NEXT + ((_TF + _RESULT + _SPREAD) if full else 0)
        chart = min(_MAX_CHART, width - fixed - 1)
        return cls(full=full, chart=chart if chart >= _MIN_CHART else 0)


def _header(columns: _Columns, rate: Decimal | None) -> Text:
    price = "price" if rate is not None else "USDT"
    out = Text(f"{'coin':<{_COIN}}")
    if columns.full:
        out.append(f"{'tf':<{_TF}}")
    out.append(f"{price:>{_PRICE - 1}} {_WINDOW:>{_CHANGE - 1}} ")
    if columns.chart:
        out.append(f"{'chart':<{columns.chart}} ")
    if columns.full:
        out.append(f"{'spread':<{_SPREAD}}{'result':<{_RESULT}}")
    out.append("next")
    return out


def _quote_cells(
    quote: LiveQuote | None, look: LiveLook, columns: _Columns, rate: Decimal | None
) -> Text:
    """Return the price, change and chart cells of one coin."""
    tokens = look.tokens
    width = _CHANGE + (columns.chart + 1 if columns.chart else 0)
    if quote is None:
        out = Text(f"{_NONE:>{_PRICE - 1}} ", style=tokens.dim)
        out.append(" " * width)
        return out
    failed = quote.failed_at is not None
    price_style = tokens.dim if failed else look.flash(quote.coin, f"bold {tokens.text}")
    out = Text()
    out.append(f"{_price(quote.price, rate):>{_PRICE - 1}}", style=price_style)
    out.append(" ")
    if failed:
        note = f"as of {quote.at.astimezone(look.ctx.tz):%H:%M:%S}"
        out.append(f"{note:<{width}}", style=tokens.dim)
        return out
    change = quote.change_pct
    colour = tokens.up if change > 0 else tokens.down if change < 0 else tokens.muted
    out.append(f"{fmt_pct(change):>{_CHANGE - 1}} ", style=look.style(colour))
    if columns.chart:
        out.append_text(braille_spark(quote.history, columns.chart, look.style(colour)))
        out.append(" ")
    return out


def _spread_cell(quote: LiveQuote | None, look: LiveLook) -> Text:
    """Return the spread cell of one coin: basis points, or a dim dash without one."""
    if quote is None or quote.failed_at is not None or quote.spread_bps is None:
        return Text(f"{_NONE:<{_SPREAD}}", style=look.tokens.dim)
    return Text(f"{f'{quote.spread_bps:.1f} bp':<{_SPREAD}}", style=look.tokens.muted)


def market_rows(
    watch: Sequence[WatchedPair],
    quotes: Mapping[str, LiveQuote],
    look: LiveLook,
    *,
    width: int,
    rate: Decimal | None,
    contexts: Mapping[str, BarContext] | None = None,
    books: Mapping[str, BookHealth] | None = None,
    now: datetime | None = None,
) -> RenderableType:
    """Return the Market panel: the live tag, then per coin price, change, chart, next check.

    Args:
        watch: The checked streams (their coin, timeframe, result and next check).
        quotes: Live quote per coin.
        look: The live layer's look.
        width: Cells available; from 64 the timeframe and the result show too.
        rate: EUR per quote unit for the prices, or ``None`` to show USDT.
        contexts: Bar context per coin; with it, a dim forming 4h bar line and a
            24 h line follow each coin's first row.
        books: Book health per coin; with it, a dim book line (depth and the
            fresh/stale flag) follows each coin's context lines.
        now: The console clock, for the time left in the forming bar.

    Returns:
        The tag line, a header and one row per stream; under 64 cells, where
        the timeframe is hidden, one row per coin with its soonest next check.
    """
    columns = _Columns.for_width(width)
    if not columns.full:
        watch = _soonest_per_coin(watch)
    tokens = look.tokens
    header = _header(columns, rate)
    header.stylize(tokens.muted)
    lines: list[RenderableType] = [look.tag(), header]
    pairs: Sequence[WatchedPair | str] = watch or [coin for coin in LIVE_COINS if coin in quotes]
    extra: dict[str, list[Text]] = {}
    for coin, quote in quotes.items():
        context = None if contexts is None else contexts.get(coin)
        if context is not None and quote.failed_at is None and now is not None:
            extra[coin] = context_lines(context, quote.price, look, width=width, rate=rate, now=now)
    for coin, health in (books or {}).items():
        if coin in quotes:
            book = book_line(health, look, width=width, rate=rate)
            if book is not None:
                extra.setdefault(coin, []).append(book)
    for pair in pairs:
        coin = pair if isinstance(pair, str) else pair.coin
        line = Text(f"{coin:<{_COIN}}", style="bold")
        if columns.full:
            line.append(f"{'' if isinstance(pair, str) else pair.timeframe:<{_TF}}")
        line.append_text(_quote_cells(quotes.get(coin), look, columns, rate))
        if isinstance(pair, str):
            lines.append(line)
            lines.extend(extra.pop(coin, []))
            continue
        if columns.full:
            line.append_text(_spread_cell(quotes.get(coin), look))
            result = pair.signal or "none"
            line.append(f"{result:<{_RESULT}}", style="bold" if pair.signal else tokens.dim)
        line.append(day_clock(look.ctx, pair.next_check_at))
        lines.append(line)
        lines.extend(extra.pop(coin, []))
    return Group(*lines)


def _plain_price(price: Decimal, rate: Decimal | None) -> str:
    return fmt_price(price * rate if rate is not None else price)


def _volume(volume: Decimal, coin: str) -> str:
    """Return a traded volume, whole units from 100, one decimal below."""
    shown = f"{volume:,.0f}" if volume >= _VOLUME_WHOLE_FROM else f"{volume:,.1f}"
    return f"{shown} {coin}"


def _left(bar: FormingBar, now: datetime, *, spaced: bool) -> str:
    """Return the time left in the bar, ``1h 40m`` (``1h40m`` when ``spaced`` is false)."""
    hours, minutes = divmod(int(bar.left(now).total_seconds()) // 60, 60)
    if not hours:
        return f"{minutes}m"
    return f"{hours}h {minutes:02d}m" if spaced else f"{hours}h{minutes:02d}m"


def _forming_options(
    bar: FormingBar, look: LiveLook, rate: Decimal | None, now: datetime
) -> list[Text]:
    """Return the forming-bar line from the fullest form to the shortest.

    ``forming  O 53,020  H 53,240  L 52,980  last 53,115 · 2h 11m left``, then
    without the label, the live price (the price column shows it) and the double
    spaces, then the range alone.
    """
    o, h, low, last = (_plain_price(v, rate) for v in (bar.open, bar.high, bar.low, bar.last))
    forms = (
        f"forming  O {o}  H {h}  L {low}  last {last} · {_left(bar, now, spaced=True)} left",
        f"O {o} H {h} L {low} · {_left(bar, now, spaced=False)} left",
        f"O {o} H {h} L {low}",
    )
    return [Text(form, style=look.tokens.dim) for form in forms]


def _day_options(coin: str, day: DayContext, look: LiveLook, rate: Decimal | None) -> list[Text]:
    """Return the 24 h line from the fullest form to the shortest.

    ``24h ▲ +1.20%  H 53,900  L 52,400  vol 1,240 BTC``, then without the volume
    and the double spaces, then the change alone.
    """
    tokens = look.tokens
    change = fmt_pct(day.change_pct)
    high, low = _plain_price(day.high, rate), _plain_price(day.low, rate)
    tails = (f"  H {high}  L {low}  vol {_volume(day.volume, coin)}", f" H {high} L {low}", "")
    options = []
    for tail in tails:
        out = Text(style=tokens.dim)
        out.append("24h ")
        out.append(change, style=_signed_style(look, day.change_pct))
        out.append(tail)
        options.append(out)
    return options


def _fitting(options: Sequence[Text], room: int) -> Text | None:
    """Return the first (fullest) option that fits ``room`` cells."""
    return next((option for option in options if option.cell_len <= room), None)


def context_lines(
    context: BarContext,
    last: Decimal,
    look: LiveLook,
    *,
    width: int,
    rate: Decimal | None,
    now: datetime,
) -> list[Text]:
    """Return the dim lines under a coin: the forming 4h bar and the 24 h context.

    Each part uses its fullest form that fits the panel, so a narrow panel drops
    the label, the live price and the volume before it drops a part.

    Args:
        context: The coin's bar context from the hourly bars.
        last: The coin's live price.
        look: The live layer's look.
        width: Cells available to the panel.
        rate: EUR per quote unit for the prices, or ``None`` to show USDT.
        now: The console clock.

    Returns:
        One line when the full forms fit side by side, else a line per part;
        a part that is missing or does not fit in any form is left out.
    """
    bar = context.forming(last, now)
    day = context.day(last)
    room = width - len(_CONTEXT_INDENT)
    forming_options = [] if bar is None else _forming_options(bar, look, rate, now)
    day_options = [] if day is None else _day_options(context.coin, day, look, rate)
    if look.paused:
        for option in (*forming_options, *day_options):
            option.stylize(look.tokens.dim)
    if forming_options and day_options:
        full, full_day = forming_options[0], day_options[0]
        if full.cell_len + _DIVIDER.cell_len + full_day.cell_len <= room:
            return [Text.assemble(_CONTEXT_INDENT, full, _DIVIDER, full_day)]
    parts = (_fitting(forming_options, room), _fitting(day_options, room))
    return [Text.assemble(_CONTEXT_INDENT, part) for part in parts if part is not None]


def _size(value: Decimal) -> str:
    """Return a money size short: ``480``, ``12.4k``, ``1.2M``."""
    if value >= _MILLION:
        return f"{value / _MILLION:.1f}M"
    if value >= _THOUSAND:
        return f"{value / _THOUSAND:.1f}k"
    return f"{value:.0f}"


def _book_options(health: BookHealth, look: LiveLook, rate: Decimal | None) -> list[Text]:
    """Return the book line from the fullest form to the shortest.

    Fresh: ``book  depth 480k · fresh``, ``depth 480k · fresh``, ``depth 480k``.
    Stale, in amber: ``book  stale 34s · depth 480k``, ``book stale 34s``,
    ``stale 34s``.
    """
    tokens = look.tokens
    depth = _size(health.depth * rate if rate is not None else health.depth)
    if health.fresh:
        forms = (f"book  depth {depth} · fresh", f"depth {depth} · fresh", f"depth {depth}")
        style = tokens.dim
    else:
        age = f"{int(health.age.total_seconds())}s"
        forms = (f"book  stale {age} · depth {depth}", f"book stale {age}", f"stale {age}")
        style = tokens.dim if look.paused else tokens.warn
    return [Text(form, style=style) for form in forms]


def book_line(
    health: BookHealth, look: LiveLook, *, width: int, rate: Decimal | None
) -> Text | None:
    """Return the dim line under a coin: the depth of its book and whether it is fresh.

    Args:
        health: The coin's book health.
        look: The live layer's look.
        width: Cells available to the panel.
        rate: EUR per quote unit for the depth, or ``None`` to show USDT.

    Returns:
        The fullest form that fits, or ``None`` when none does.
    """
    part = _fitting(_book_options(health, look, rate), width - len(_CONTEXT_INDENT))
    return None if part is None else Text.assemble(_CONTEXT_INDENT, part)


def latency_chip(
    round_trips: Sequence[Decimal], age: timedelta | None, tokens: Tokens, *, room: int
) -> Text:
    """Return the status row's latency chip: the last round trip, its sparkline, the price age.

    ``84ms ⣀⣤⣶⣤ 3s`` with the sparkline, ``84ms 3s`` without it.

    Args:
        round_trips: Recent book-call round trips in milliseconds, oldest first.
        age: Age of the newest live price, or ``None`` before the first one.
        tokens: The active design tokens.
        room: Cells the chip may use, a leading space included.

    Returns:
        The fullest form that fits, or an empty text when none does or there is
        no round trip yet.
    """
    if not round_trips:
        return Text("")
    last = f"{int(round_trips[-1])}ms"
    seen = "" if age is None else f" {int(age.total_seconds())}s"
    full = Text(" ").append(f"{last} ", style=tokens.dim)
    full.append_text(braille_spark(round_trips, _LATENCY_SPARK, tokens.muted))
    full.append(seen, style=tokens.dim)
    short = Text(f" {last}{seen}", style=tokens.dim)
    return next((chip for chip in (full, short) if chip.cell_len <= room), Text(""))


def _soonest_per_coin(watch: Sequence[WatchedPair]) -> list[WatchedPair]:
    """Return one stream per coin, the one checked next, in first-seen coin order."""
    soonest: dict[str, WatchedPair] = {}
    for pair in watch:
        kept = soonest.get(pair.coin)
        if kept is None or pair.next_check_at < kept.next_check_at:
            soonest[pair.coin] = pair
    return list(soonest.values())


def _signed_style(look: LiveLook, value: Decimal) -> str:
    tokens = look.tokens
    colour = tokens.up if value > 0 else tokens.down if value < 0 else tokens.muted
    return look.style(colour)


def live_portfolio_lines(
    summary: PortfolioSummary, live: LiveTotal, look: LiveLook, *, total_shown: bool
) -> list[Text]:
    """Return the live line and the booked label above the booked portfolio head.

    Args:
        summary: The booked portfolio view (its range and booked total).
        live: The portfolio at live prices.
        look: The live layer's look.
        total_shown: The big number already shows the live total, so the live
            line leaves it out and the booked line names the booked total.

    Returns:
        ``● live · today ▲ +€14.64 +1.45%`` (with the live total when the big
        number is hidden) and ``booked as of 16:00 · €1,021.06``.
    """
    line = look.tag()
    if not total_shown:
        line.append(f"  {fmt_level(live.total)}", style=look.style(f"bold {look.tokens.text}"))
    line.append(f" · {range_label(summary.range)} ", style=look.tokens.muted)
    line.append(fmt_change(live.change, live.change_pct), style=_signed_style(look, live.change))
    booked = look.booked()
    if total_shown:
        booked.append(f" · {fmt_level(summary.total_value)}", style=look.tokens.muted)
    return [line, booked]


def live_detail_line(position: LivePosition, look: LiveLook) -> Text:
    """Return the live line above a position's booked detail.

    Args:
        position: The position at its live price.
        look: The live layer's look.

    Returns:
        ``● live · now €2,301 · ▲ +2.61% · gross ▲ +€11.20 · net ▲ +€9.10``
        then ``booked as of 16:00 below``; EUR-pending positions show the USDT
        price and no money.
    """
    line = look.tag()
    now = (
        fmt_usdt_price(position.price)
        if position.price_eur is None
        else fmt_eur_price(position.price_eur)
    )
    line.append(" · now ", style=look.tokens.muted)
    line.append(now, style=look.flash(position.coin, f"bold {look.tokens.text}"))
    line.append(" · ", style=look.tokens.muted)
    line.append(fmt_pct(position.change_pct), style=_signed_style(look, position.change_pct))
    for label, value in (("gross", position.gross), ("net", position.net)):
        if value is not None:
            line.append(f" · {label} ", style=look.tokens.muted)
            line.append(fmt_eur(value), style=_signed_style(look, value))
    booked = look.booked()
    booked.append(" below", style=look.tokens.muted)
    return Text.assemble(line, "\n", booked)
