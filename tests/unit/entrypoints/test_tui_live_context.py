"""The Market panel shows the forming 4h bar and the 24 h context under each coin (issue #1289).

Invariants:

- under a coin with a live price and a bar context sit a forming-bar line
  (open, high, low, last, time left) and a 24 h line (change, high, low, volume),
  with every figure worked out by hand from the hourly-bar fixture;
- both parts share one line when the panel is wide enough, else each takes a line;
  a narrow panel drops the label, the live price and the volume before it drops
  a part, and a part that does not fit in any form is left out;
- the context lines come once per coin, under its first row, and never for a
  coin whose last fetch failed or that has no context;
- a paused feed dims every part but keeps the figures;
- through the app: a ticker that can also give bars fills the lines, one that
  cannot leaves the panel as before, and a failing bars source never reaches the UI.
"""

import asyncio
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from rich.console import Group
from rich.text import Text

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Surface
from hermes.application.console.live import LiveQuote, LiveState, LiveStatus
from hermes.application.console.live_context import BarContext, bar_context
from hermes.application.console.render import RenderContext, to_plain_text
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_live import LiveLook, market_rows
from hermes.entrypoints.tui_theme import BASE
from tests.support.clock import FakeClock
from tests.support.hourly_bars import hourly_bars

_NOW = datetime(2026, 10, 19, 14, 20, tzinfo=UTC)
_FORMING = "forming  O 122.0  H 129.0  L 117.0  last 126.0 · 1h 40m left"
_DAY = "24h ▲ +26.00%  H 129.0  L 95.00  vol 50.0 BTC"


def _context(coin: str = "BTC") -> BarContext:
    context = bar_context(coin, hourly_bars(coin, _NOW), _NOW)
    assert context is not None
    return context


def _look(state: LiveState = LiveState.LIVE) -> LiveLook:
    model = FixtureReadModel.load("ok-two-positions")
    ctx = replace(RenderContext.for_model(model, surface=Surface.OPERATOR_TUI), demo_scenario=None)
    return LiveLook(
        status=LiveStatus(state, _NOW), tokens=BASE, dot="#00F0FF", ctx=ctx, booked_at=_NOW
    )


def _quote(coin: str = "BTC", *, failed: bool = False) -> LiveQuote:
    return LiveQuote(
        coin=coin,
        price=Decimal(126),
        at=_NOW,
        previous=None,
        history=(Decimal(125), Decimal(126)),
        failed_at=_NOW + timedelta(seconds=10) if failed else None,
    )


def _rows(width: int, **overrides: object) -> Group:
    model = FixtureReadModel.load("ok-two-positions")
    args: dict[str, object] = {
        "watch": model.market_watch(),
        "quotes": {"BTC": _quote()},
        "look": _look(),
        "width": width,
        "rate": None,
        "contexts": {"BTC": _context()},
        "now": _NOW,
    }
    args.update(overrides)
    rows = market_rows(**args)  # type: ignore[arg-type]
    assert isinstance(rows, Group)
    return rows


def _lines(rows: Group, width: int) -> list[str]:
    return to_plain_text(rows, width=width).splitlines()


def _context_lines(width: int, **overrides: object) -> list[str]:
    """Return the lines of the panel that belong to the context (they start indented)."""
    return [line for line in _lines(_rows(width, **overrides), width) if line.startswith("     ")]


def test_both_parts_share_one_line_when_the_panel_is_wide() -> None:
    """At 140 cells the forming bar and the 24 h context sit on one line, divided by a bar."""
    assert _context_lines(140) == [f"     {_FORMING} │ {_DAY}"]


def test_the_24h_context_takes_its_own_line_from_100_cells() -> None:
    """At 100 cells the two parts do not fit side by side, so each has a line."""
    assert _context_lines(100) == [f"     {_FORMING}", f"     {_DAY}"]


def test_a_narrow_panel_drops_the_label_the_live_price_and_the_volume() -> None:
    """At 44 cells (the Market panel beside Activity) the short forms of both parts are used."""
    assert _context_lines(44) == [
        "     O 122.0 H 129.0 L 117.0 · 1h40m left",
        "     24h ▲ +26.00% H 129.0 L 95.00",
    ]


def test_a_very_narrow_panel_keeps_the_range_and_the_change_alone() -> None:
    """At 32 cells only the open-high-low range and the 24 h change fit."""
    assert _context_lines(32) == ["     O 122.0 H 129.0 L 117.0", "     24h ▲ +26.00%"]


def test_nothing_shows_when_not_even_the_shortest_form_fits() -> None:
    """At 10 cells nothing fits, so both parts are left out."""
    assert _context_lines(10) == []


def test_the_context_lines_sit_directly_under_the_coin() -> None:
    """The coin row comes first, then its context, then the next coin."""
    rows = [line.split()[0] if line.strip() else "" for line in _lines(_rows(140), 140)]
    btc = rows.index("BTC")
    assert rows[btc + 1] == "forming"


def test_a_coin_with_two_streams_gets_its_context_once() -> None:
    """BTC checked on 4h and 1d: the lines follow the first BTC row only."""
    watch = list(FixtureReadModel.load("ok-two-positions").market_watch())
    btc = next(pair for pair in watch if pair.coin == "BTC")
    watch.insert(1, replace(btc, timeframe="1d"))
    assert _context_lines(140, watch=tuple(watch)) == [f"     {_FORMING} │ {_DAY}"]


def test_a_failed_coin_shows_no_context() -> None:
    """After a failed tick the live price is stale, so the lines that use it are left out."""
    assert _context_lines(140, quotes={"BTC": _quote(failed=True)}) == []


def test_a_coin_without_a_context_shows_none() -> None:
    """No context for BTC: the panel is what it was before this change."""
    assert _context_lines(140, contexts={}) == []


def test_a_bar_that_closed_since_the_fetch_leaves_only_the_24h_line() -> None:
    """At 16:00 the 12:00 bar is over; the 24 h line stays on its own."""
    later = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
    assert _context_lines(120, now=later)[0].startswith("     24h ")


def test_a_paused_feed_dims_every_part_and_keeps_the_figures() -> None:
    """Paused: the same text, every span in the dim token ."""
    rows = _rows(140, look=_look(LiveState.PAUSED))
    line = next(r for r in rows.renderables if isinstance(r, Text) and "forming" in r.plain)
    styles = {str(span.style) for span in line.spans} | {str(line.style)} - {""}
    assert (_FORMING in line.plain, styles) == (True, {BASE.dim})


def test_the_prices_show_in_eur_at_the_booked_rate() -> None:
    """At 0.5 EUR per USDT, open 122 reads 61.00 and the high 129 reads 64.50."""
    lines = _context_lines(140, rate=Decimal("0.5"))
    assert tuple(part for part in ("O 61.00", "H 64.50") if part in lines[0]) == (
        "O 61.00",
        "H 64.50",
    )


# ----------------------------------------------------------------------
# Through the app
# ----------------------------------------------------------------------

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)


class _Ticker:
    """A price table; with ``bars`` it can also give hourly bars."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.bar_calls = 0

    def get_ticker(self, pair: Pair) -> Ticker:
        price = Decimal(126)
        return Ticker(
            pair=pair,
            best_bid=price - 1,
            best_ask=price + 1,
            last_price=price,
            snapshot_time=_FROZEN,
        )


class _TickerWithBars(_Ticker):
    def fetch_klines(
        self, *, pair: Pair, interval: KlineInterval, since: datetime, until: datetime
    ):
        self.bar_calls += 1
        if self.fail:
            raise KrakenPublicError("no bars")
        bars: list[Kline] = hourly_bars(pair.base.value, _FROZEN)
        yield from (bar for bar in bars if since <= bar.open_time < until)


def _market_text(ticker: _Ticker) -> str:
    async def drive() -> str:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=FakeClock(_FROZEN),
            motion=False,
            refresh_seconds=3600,
            exposure_cap=Decimal("0.15"),
            ticker=ticker,
        )
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return app.plain_text("market")

    return asyncio.run(drive())


def test_a_ticker_that_gives_bars_fills_the_context_lines() -> None:
    """At 12:12 the 4h bar is the 12:00 hour alone and 3h 48m are left; 126 is up 26 % on 100.

    The Market panel is 44 cells wide beside Activity, so the short forms show,
    in EUR at the fixture's booked rate.
    """
    text = _market_text(_TickerWithBars())
    forming = re.search(r"O [\d.,]+ H [\d.,]+ L [\d.,]+ · 3h48m left", text)
    assert (forming is not None, "24h ▲ +26.00% H " in text) == (True, True)


def test_a_ticker_without_bars_leaves_the_panel_as_before() -> None:
    """No fetch_klines: no forming line, no 24 h line, the prices are still there."""
    text = _market_text(_Ticker())
    assert ("forming" in text, "24h" in text, "BTC" in text) == (False, False, True)


def test_a_failing_bars_source_never_reaches_the_ui() -> None:
    """The bars source raising leaves the live prices on screen and no context line."""
    ticker = _TickerWithBars(fail=True)
    text = _market_text(ticker)
    assert (ticker.bar_calls > 0, "forming" in text, "BTC" in text) == (True, False, True)
