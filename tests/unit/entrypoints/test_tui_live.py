"""The live layer's look and the Market rows (issue #713), as pure functions.

Invariants:

- the tag reads ``● live`` while live, ``live · paused HH:MM:SS`` once stale,
  and says it is waiting before the first tick;
- a flashing cell is blue on up and orange on down; paused cells never flash
  and are dim;
- Market rows show each coin's live price, change and braille sparkline; a coin
  whose last fetch failed keeps its last price, dimmed, with "as of HH:MM:SS";
  a coin with no tick yet shows no invented price;
- under 64 cells, where the timeframe is hidden, a coin checked on two
  timeframes is one row with its soonest next check; from 64 cells each
  stream keeps its own row;
- prices show in EUR at the booked rate, else in USDT;
- live position rows carry the live tag and a booked line; paused rows are dim;
  a blind surface gets no live money at all (``pnl_visible``);
- the portfolio and detail live lines name both layers.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from rich.console import Group
from rich.text import Text

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import PositionRow, Surface
from hermes.application.console.format import fmt_pct
from hermes.application.console.live import (
    LiveQuote,
    LiveState,
    LiveStatus,
    LiveTotal,
    live_position,
)
from hermes.application.console.render import RenderContext, to_plain_text
from hermes.application.console.views import WatchedPair
from hermes.entrypoints.tui_home import LiveRows, position_rows
from hermes.entrypoints.tui_live import (
    LiveLook,
    eur_rate,
    live_detail_line,
    live_portfolio_lines,
    live_tag,
    market_rows,
)
from hermes.entrypoints.tui_theme import BASE

_TICK = datetime(2026, 10, 3, 12, 12, 5, tzinfo=UTC)
_BOOKED = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_DOT = "#00F0FF"


def _model() -> FixtureReadModel:
    return FixtureReadModel.load("ok-two-positions")


def _ctx(surface: Surface = Surface.OPERATOR_TUI) -> RenderContext:
    return replace(RenderContext.for_model(_model(), surface=surface), demo_scenario=None)


def _look(
    state: LiveState = LiveState.LIVE,
    flashes: dict[str, int] | None = None,
    surface: Surface = Surface.OPERATOR_TUI,
) -> LiveLook:
    at = None if state is LiveState.WAITING else _TICK
    return LiveLook(
        status=LiveStatus(state, at),
        tokens=BASE,
        dot=_DOT,
        ctx=_ctx(surface),
        flashes=flashes or {},
        booked_at=_BOOKED,
    )


def _quote(coin: str, *prices: str, failed: bool = False, spread: str | None = None) -> LiveQuote:
    history = tuple(Decimal(p) for p in prices)
    return LiveQuote(
        coin=coin,
        price=history[-1],
        at=_TICK,
        previous=history[-2] if len(history) > 1 else None,
        history=history,
        failed_at=_TICK + timedelta(seconds=10) if failed else None,
        spread_bps=None if spread is None else Decimal(spread),
    )


def _plain(renderable: object, width: int = 120) -> str:
    return to_plain_text(renderable, width=width)  # type: ignore[arg-type]


def _spans(renderable: Group | Text) -> list[tuple[str, str]]:
    """Return every (text, style) span of a group of texts."""
    texts = renderable.renderables if isinstance(renderable, Group) else [renderable]
    out = []
    for text in texts:
        assert isinstance(text, Text)
        out += [(text.plain[s.start : s.end], str(s.style)) for s in text.spans]
    return out


# ----------------------------------------------------------------------
# Tag and flash
# ----------------------------------------------------------------------


def test_tag_reads_live_with_the_pulsing_dot_colour() -> None:
    """Live: ``● live``, the dot in the pulse colour and the word in the live token."""
    tag = live_tag(LiveStatus(LiveState.LIVE, _TICK), _DOT, BASE, _ctx())
    assert (tag.plain, _spans(tag)) == (
        "● live",
        [("● ", f"bold {_DOT}"), ("live", f"bold {BASE.live}")],
    )


def test_tag_reads_paused_with_the_last_tick_time() -> None:
    """Paused: ``● live · paused 14:12:05`` (Madrid time of the last good tick)."""
    tag = live_tag(LiveStatus(LiveState.PAUSED, _TICK), _DOT, BASE, _ctx())
    assert tag.plain == "● live · paused 14:12:05"


def test_tag_says_it_waits_before_the_first_tick() -> None:
    """Waiting: an open dot and the words, all dim."""
    tag = live_tag(LiveStatus(LiveState.WAITING), _DOT, BASE, _ctx())
    assert (tag.plain, str(tag.style)) == ("○ live · waiting for prices", BASE.dim)


def test_flash_is_blue_up_and_orange_down() -> None:
    """A rising coin's cell is on the up token, a falling one on the down token."""
    look = _look(flashes={"ETH": 1, "SOL": -1})
    assert (look.flash("ETH", "x"), look.flash("SOL", "x"), look.flash("BTC", "x")) == (
        f"bold {BASE.void} on {BASE.up}",
        f"bold {BASE.void} on {BASE.down}",
        "x",
    )


def test_paused_cells_are_dim_and_never_flash() -> None:
    """Paused, every live cell takes the dim token, even one listed as flashing."""
    look = _look(LiveState.PAUSED, flashes={"ETH": 1})
    assert (look.flash("ETH", "x"), look.style("x")) == (BASE.dim, BASE.dim)


def test_booked_label_names_the_last_bar_close() -> None:
    """``booked as of 14:00`` in Madrid time."""
    assert _look().booked().plain == "booked as of 14:00"


# ----------------------------------------------------------------------
# Market rows
# ----------------------------------------------------------------------


def test_market_rows_show_price_change_and_sparkline_per_coin() -> None:
    """At 44 cells: tag, header, then BTC at EUR, its window change and a braille chart."""
    watch = _model().market_watch()
    quotes = {"BTC": _quote("BTC", "60000", "61200")}
    text = _plain(market_rows(watch, quotes, _look(), width=44, rate=Decimal("0.5")), width=44)
    lines = text.splitlines()
    assert lines[0] == "● live"
    assert lines[1].split() == ["coin", "price", "15m", "chart", "next"]
    assert lines[2].split()[:4] == ["BTC", "€30,600", "▲", "+2.00%"]
    assert lines[2].split()[4] == "⣀⣀⣀⣸⣿⣿⣿"


def test_market_rows_add_timeframe_spread_and_result_from_64_cells() -> None:
    """At 70 cells the header also names the timeframe, the spread and the result."""
    watch = _model().market_watch()
    text = _plain(market_rows(watch, {}, _look(), width=70, rate=None), width=70)
    assert text.splitlines()[1].split() == [
        "coin",
        "tf",
        "USDT",
        "15m",
        "chart",
        "spread",
        "result",
        "next",
    ]


def test_market_rows_show_the_spread_in_basis_points_from_64_cells() -> None:
    """At 100 cells BTC reads ``3.4 bp`` under the spread header; at 44 cells no spread shows."""
    watch = _model().market_watch()
    quotes = {"BTC": _quote("BTC", "60000", "61200", spread="3.449")}
    wide = _plain(market_rows(watch, quotes, _look(), width=100, rate=None), width=100)
    narrow = _plain(market_rows(watch, quotes, _look(), width=44, rate=None), width=44)
    btc = next(line for line in wide.splitlines() if line.startswith("BTC"))
    assert ("3.4 bp" in btc, " bp" in narrow) == (True, False)


def test_a_failed_coin_shows_no_spread() -> None:
    """After a failed tick the old spread is stale: the cell is a dash, not the last figure."""
    watch = _model().market_watch()
    quotes = {"BTC": _quote("BTC", "60000", "61200", failed=True, spread="3.449")}
    wide = _plain(market_rows(watch, quotes, _look(), width=100, rate=None), width=100)
    assert " bp" not in wide


def test_failed_coin_keeps_its_last_price_dimmed_with_as_of() -> None:
    """ETH failed after 2600: the row keeps 2,600 USDT, dim, and says ``as of 14:12:05``."""
    watch = _model().market_watch()
    quotes = {"ETH": _quote("ETH", "2590", "2600", failed=True)}
    rows = market_rows(watch, quotes, _look(), width=44, rate=None)
    eth = next(line for line in _plain(rows, 44).splitlines() if line.startswith("ETH"))
    assert eth.split()[:4] == ["ETH", "2,600", "as", "of"]
    assert "14:12:05" in eth
    assert ("     2,600", BASE.dim) in _spans(rows)  # type: ignore[arg-type]


def test_coin_without_a_tick_shows_no_price() -> None:
    """SOL has never ticked: an ellipsis, never zero."""
    watch = _model().market_watch()
    text = _plain(market_rows(watch, {}, _look(), width=44, rate=None), 44)
    sol = next(line for line in text.splitlines() if line.startswith("SOL"))
    assert sol.split()[:2] == ["SOL", "…"]


def test_eur_rate_comes_from_the_first_priced_position() -> None:
    """The first EUR-priced row's rate wins over the portfolio's."""
    model = _model()
    rows = model.positions()
    assert eur_rate(rows, model.portfolio()) == rows[0].eur_per_usdt


def test_eur_rate_is_unknown_without_a_priced_row_or_quote_total() -> None:
    """No position and no quote total: prices stay in USDT."""
    summary = replace(_model().portfolio(), total_value_quote=None)
    assert (eur_rate([], summary), eur_rate([], None)) == (None, None)


# ----------------------------------------------------------------------
# Position rows, portfolio and detail lines
# ----------------------------------------------------------------------


def _live_rows(look: LiveLook, rows: list[PositionRow], price: Decimal) -> LiveRows:
    model = _model()
    return LiveRows(
        look=look,
        positions={
            row.coin: live_position(row, model.position(row.coin), price) for row in rows[:1]
        },
        ticks={rows[0].coin: (price,)},
    )


def test_live_position_rows_carry_the_tag_and_the_booked_line() -> None:
    """The header ends with ``● live``; the last line keeps the booked net per coin."""
    model = _model()
    rows = list(model.positions())
    details = {row.coin: model.position(row.coin) for row in rows}
    ctx = _ctx()
    live = _live_rows(_look(), rows, rows[0].current_price * Decimal("1.1"))
    text = _plain(position_rows(rows, details, ctx, BASE, width=110, live=live), width=110)
    lines = text.splitlines()
    assert lines[0].endswith("● live")
    assert lines[-1] == "booked as of 14:00 · net ETH ▲ +€1.54 · SOL ▲ +€6.05"


def test_live_row_values_the_position_at_the_live_price() -> None:
    """ETH at +10% over its mark: the row's change is the live change, not the booked one."""
    model = _model()
    rows = list(model.positions())
    price = rows[0].current_price * Decimal("1.1")
    live = _live_rows(_look(), rows, price)
    expected = live.positions[rows[0].coin].change_pct
    text = _plain(position_rows(rows, {}, _ctx(), BASE, width=110, live=live), width=110)
    eth = text.splitlines()[1]
    assert f"+{expected:.2f}%" in eth
    assert f"+{rows[0].change_pct:.2f}%" not in eth


def test_paused_live_rows_are_dim() -> None:
    """Paused: the live change cell takes the dim token."""
    model = _model()
    rows = list(model.positions())
    live = _live_rows(_look(LiveState.PAUSED), rows, rows[0].current_price * Decimal("1.1"))
    group = position_rows(rows, {}, _ctx(), BASE, width=110, live=live)
    change = live.positions[rows[0].coin].change_pct
    assert (fmt_pct(change), BASE.dim) in _spans(group)


def test_blind_surface_gets_no_live_money() -> None:
    """On a P&L-blind surface the rows are the shared table: no tag, no euro, no booked line."""
    model = _model()
    rows = list(model.positions())
    live = _live_rows(_look(surface=Surface.SUPERVISOR_DESK), rows, Decimal(9999))
    ctx = _ctx(Surface.SUPERVISOR_DESK)
    text = _plain(position_rows(rows, {}, ctx, BASE, width=110, live=live), width=110)
    assert ("live" in text, "€" in text, "booked" in text) == (False, False, False)


def test_portfolio_live_lines_name_both_layers() -> None:
    """With the big number showing the live total: the live change, then the booked total."""
    summary = _model().portfolio()
    live = LiveTotal(total=Decimal("1030.50"), change=Decimal("21.74"), change_pct=Decimal("2.15"))
    lines = live_portfolio_lines(summary, live, _look(), total_shown=True)
    assert [line.plain for line in lines] == [
        "● live · today ▲ +€21.74 +2.15%",
        "booked as of 14:00 · €1,021.06",
    ]


def test_portfolio_live_line_carries_the_total_when_the_big_number_is_hidden() -> None:
    """Short terminals: the live line names the live total itself."""
    summary = _model().portfolio()
    live = LiveTotal(total=Decimal("1030.50"), change=Decimal("21.74"), change_pct=Decimal("2.15"))
    lines = live_portfolio_lines(summary, live, _look(), total_shown=False)
    assert [line.plain for line in lines] == [
        "● live  €1,030.50 · today ▲ +€21.74 +2.15%",
        "booked as of 14:00",
    ]


def test_detail_live_line_shows_now_change_gross_and_net() -> None:
    """The detail's live line, then where the booked figures are."""
    model = _model()
    row = model.positions()[0]
    position = live_position(row, model.position(row.coin), row.current_price)
    text = live_detail_line(position, _look()).plain.splitlines()
    assert text[0].startswith("● live · now €")
    assert (" · gross ▲ " in text[0], " · net ▲ " in text[0], text[1]) == (
        True,
        True,
        "booked as of 14:00 below",
    )


def _stream(coin: str, timeframe: str, next_check_at: datetime) -> WatchedPair:
    return WatchedPair(
        coin=coin,
        quote="USDT",
        timeframe=timeframe,
        last_check_at=next_check_at - timedelta(hours=4),
        signal=None,
        next_check_at=next_check_at,
    )


_TWO_TIMEFRAMES = (
    _stream("BTC", "1d", datetime(2026, 10, 4, 0, 0, tzinfo=UTC)),
    _stream("BTC", "4h", datetime(2026, 10, 3, 16, 0, tzinfo=UTC)),
    _stream("ETH", "1d", datetime(2026, 10, 4, 0, 0, tzinfo=UTC)),
    _stream("ETH", "4h", datetime(2026, 10, 3, 16, 0, tzinfo=UTC)),
)


def test_narrow_market_shows_each_coin_once_with_its_soonest_check() -> None:
    """At 44 cells BTC and ETH on 4h and 1d are one row each, next check 18:00 Madrid."""
    text = _plain(market_rows(_TWO_TIMEFRAMES, {}, _look(), width=44, rate=None), 44)
    rows = [line.split() for line in text.splitlines()[2:]]
    assert [(row[0], row[-1]) for row in rows] == [("BTC", "18:00"), ("ETH", "18:00")]


def test_full_width_market_keeps_one_row_per_stream() -> None:
    """At 70 cells the timeframe column tells the streams apart, so all four rows stay."""
    text = _plain(market_rows(_TWO_TIMEFRAMES, {}, _look(), width=70, rate=None), 70)
    rows = [line.split()[:2] for line in text.splitlines()[2:]]
    assert rows == [["BTC", "1d"], ["BTC", "4h"], ["ETH", "1d"], ["ETH", "4h"]]
