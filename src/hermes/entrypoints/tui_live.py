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
from datetime import datetime
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
from hermes.application.console.render import RenderContext, day_clock, range_label
from hermes.entrypoints.tui_charts import braille_spark
from hermes.entrypoints.tui_theme import LIVE_GLYPH, Tokens

__all__ = [
    "LiveLook",
    "eur_rate",
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
) -> RenderableType:
    """Return the Market panel: the live tag, then per coin price, change, chart, next check.

    Args:
        watch: The checked streams (their coin, timeframe, result and next check).
        quotes: Live quote per coin.
        look: The live layer's look.
        width: Cells available; from 64 the timeframe and the result show too.
        rate: EUR per quote unit for the prices, or ``None`` to show USDT.

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
    for pair in pairs:
        coin = pair if isinstance(pair, str) else pair.coin
        line = Text(f"{coin:<{_COIN}}", style="bold")
        if columns.full:
            line.append(f"{'' if isinstance(pair, str) else pair.timeframe:<{_TF}}")
        line.append_text(_quote_cells(quotes.get(coin), look, columns, rate))
        if isinstance(pair, str):
            lines.append(line)
            continue
        if columns.full:
            line.append_text(_spread_cell(quotes.get(coin), look))
            result = pair.signal or "none"
            line.append(f"{result:<{_RESULT}}", style="bold" if pair.signal else tokens.dim)
        line.append(day_clock(look.ctx, pair.next_check_at))
        lines.append(line)
    return Group(*lines)


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
