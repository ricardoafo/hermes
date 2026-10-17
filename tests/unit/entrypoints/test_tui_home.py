"""The Cockpit home content and its breakpoint choice (issue #712), as pure functions.

Invariants:

- the breakpoint tokens pick one column under 100 columns, two from 100, the
  mock layout (Market beside Activity) from 160 and three columns from 220;
  under 30 rows the curve hides; the big total shows only from 100 columns and
  30 rows (compact keeps the plain bold total line); from 45 rows the curve doubles;
- every money figure obeys ``pnl_visible``: a blind surface gets no total, no
  curve, no meter and no net;
- exposure is invested over the start capital and its meter is measured
  against the roster cap, amber from 80% of it, and says when the cap is unknown;
- a position's stop-room meter is amber when the stop is within 2 ATR;
- the empty state names what hermes watches and when it looks again;
- the log tail says why it is empty (demo, no journal) or shows the last lines;
- the 90-day line ends with a bar and the percentage.
"""

# The console prints the typographic minus (U+2212) on purpose.
# ruff: noqa: RUF001, RUF002

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import ShadowTest, ShadowTestStatus, Surface
from hermes.application.console.logs import LogLine
from hermes.application.console.render import RenderContext, to_plain_text
from hermes.entrypoints.tui_home import (
    LOGS_DEMO,
    LOGS_UNAVAILABLE,
    HeightClass,
    WidthClass,
    empty_positions,
    exposure_share,
    home_layout,
    log_tail,
    portfolio_curve,
    portfolio_meters,
    position_rows,
    run_line,
    total_text,
)
from hermes.entrypoints.tui_theme import BASE

_CAP = Decimal("0.15")


def _model() -> FixtureReadModel:
    return FixtureReadModel.load("ok-two-positions")


def _ctx(surface: Surface = Surface.OPERATOR_TUI) -> RenderContext:
    return replace(RenderContext.for_model(_model(), surface=surface), demo_scenario=None)


def _plain(renderable: object, width: int = 100) -> str:
    return to_plain_text(renderable, width=width)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Breakpoints
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((80, 24), (WidthClass.COMPACT, HeightClass.SHORT, 1, 0, True, False, False, 0)),
        ((99, 30), (WidthClass.COMPACT, HeightClass.TALL, 1, 0, True, False, False, 0)),
        ((100, 29), (WidthClass.STANDARD, HeightClass.SHORT, 2, 0, False, False, False, 0)),
        ((120, 36), (WidthClass.STANDARD, HeightClass.TALL, 2, 5, False, True, False, 0)),
        ((160, 45), (WidthClass.WIDE, HeightClass.TOWERING, 2, 12, False, True, True, 0)),
        ((219, 44), (WidthClass.WIDE, HeightClass.TALL, 2, 6, False, True, True, 0)),
        ((240, 60), (WidthClass.ULTRA, HeightClass.TOWERING, 3, 12, False, True, True, 20)),
    ],
)
def test_breakpoint_choice(size: tuple[int, int], expected: tuple[object, ...]) -> None:
    """Width picks the columns and the chart kind; height hides or doubles the curve."""
    layout = home_layout(*size)
    assert (
        layout.width_class,
        layout.height_class,
        layout.columns,
        layout.curve_rows,
        layout.sparklines,
        layout.big_total,
        layout.market_beside_activity,
        layout.log_rows,
    ) == expected


def test_columns_fill_the_width_with_one_cell_gutters() -> None:
    """At 240 columns: a 1-cell scrollbar, Portfolio 60, then the rest 3:2 after 1-cell gutters."""
    layout = home_layout(240, 60)
    assert (layout.left, layout.right, layout.third, layout.classes) == (
        60,
        107,
        70,
        ("-ultra", "-towering"),
    )


# ----------------------------------------------------------------------
# Portfolio
# ----------------------------------------------------------------------


def test_total_text_is_the_eur_level_on_the_operator_surface() -> None:
    """The big total is the total value in EUR."""
    assert total_text(_model().portfolio(), _ctx()) == "€1,021.06"


def test_blind_surface_gets_no_total_curve_or_meters() -> None:
    """On Slack (blind) the total is None and the curve and meters draw nothing."""
    summary, blind = _model().portfolio(), _ctx(Surface.SLACK)
    assert (
        total_text(summary, blind),
        _plain(portfolio_curve(summary, blind, BASE, width=20, rows=3)),
        _plain(portfolio_meters(summary, blind, BASE, width=60, cap=_CAP)),
    ) == (None, "\n", "")


def test_rising_range_draws_the_curve_in_the_gain_gradient() -> None:
    """The fixture's day rises, so the top row is the gain gradient's bright stop.

    The curve is 3 braille rows; the axis and caption lines under it (issue
    #713) are not curve rows.
    """
    curve = portfolio_curve(_model().portfolio(), _ctx(), BASE, width=12, rows=3)
    lines = curve.split("\n")
    braille = [line for line in lines if line.plain and "⠀" <= line.plain[0] <= "⣿"]
    assert (len(braille), {str(span.style) for span in lines[0].spans}) == (
        3,
        {BASE.gain[-1]},
    )


def test_curve_ends_with_its_time_axis_and_caption_in_dim() -> None:
    """Under the curve: the six-hourly axis, then the caption, both in the dim token.

    The fixture's today range starts at local midnight and holds 6 samples
    that rise to the high; at 40 cells the high is left out.
    """
    curve = portfolio_curve(_model().portfolio(), _ctx(), BASE, width=40, rows=3)
    *_, axis, caption = curve.split("\n")
    styles = {str(span.style) for span in (*axis.spans, *caption.spans)}
    assert (axis.plain.split(), caption.plain, styles) == (
        ["00", "06", "12"],
        "today · 6 checks · €1,009 → €1,021",
        {BASE.dim},
    )


def test_exposure_is_invested_over_the_start_capital() -> None:
    """€230.70 invested of a €1,000.00 start is 0.2307."""
    assert exposure_share(_model().portfolio()) == Decimal("0.2307")


def test_exposure_meter_names_the_cap_and_is_amber_near_it() -> None:
    """At 0.2307 against a 15% cap the line reads '23.1% of 15% cap' in amber."""
    group = portfolio_meters(_model().portfolio(), _ctx(), BASE, width=60, cap=_CAP)
    exposure = group.renderables[-1]
    assert (
        _plain(exposure).rstrip().endswith("23.1% of 15% cap"),
        exposure.spans[-1].style,  # type: ignore[union-attr]
    ) == (True, BASE.warn)


def test_exposure_meter_is_the_gain_gradient_below_the_warning_share() -> None:
    """€50 invested of €1,000 is a third of the 15% cap: no amber anywhere on the line."""
    summary = replace(_model().portfolio(), invested=Decimal("50.00"))
    exposure = portfolio_meters(summary, _ctx(), BASE, width=60, cap=_CAP).renderables[-1]
    styles = {str(span.style) for span in exposure.spans}  # type: ignore[union-attr]
    assert (BASE.warn in styles, _plain(exposure).rstrip().endswith("5.0% of 15% cap")) == (
        False,
        True,
    )


def test_unknown_cap_is_said_not_guessed() -> None:
    """Without a roster cap the exposure line says so and draws no meter."""
    exposure = portfolio_meters(_model().portfolio(), _ctx(), BASE, width=60, cap=None)
    assert _plain(exposure.renderables[-1]).strip() == ("exposure 23.1% · cap unknown")


def test_cash_and_invested_meters_carry_level_and_share() -> None:
    """The cash line ends with €790.36 and 77.4%; the invested line with €230.70 and 22.6%."""
    group = portfolio_meters(_model().portfolio(), _ctx(), BASE, width=60, cap=_CAP)
    cash, invested = (_plain(line).split() for line in group.renderables[:2])
    assert (cash[0], cash[-2:], invested[0], invested[-2:]) == (
        "cash",
        ["€790.36", "77.4%"],
        "invested",
        ["€230.70", "22.6%"],
    )


# ----------------------------------------------------------------------
# Positions
# ----------------------------------------------------------------------


def _rows_text(width: int, selected: str | None = "SOL") -> list[str]:
    model = _model()
    rows = model.positions()
    details = {row.coin: model.position(row.coin) for row in rows}
    group = position_rows(rows, details, _ctx(), BASE, width=width, selected=selected)
    return _plain(group, width=width + 2).splitlines()


def test_position_rows_mark_the_selected_coin() -> None:
    """The cursor row reads '▸SOL'; the other row starts with a space."""
    lines = _rows_text(100)
    assert (lines[1].startswith(" ETH"), lines[2].startswith("▸SOL")) == (True, True)


def test_wide_position_row_shows_prices_change_net_and_stop_room() -> None:
    """From 92 cells: entry → now, the change, the net and 'stop −3.0%'."""
    sol = _rows_text(100)[2]
    assert (
        "→" in sol,
        "▲ +5.40%" in sol,
        "▲ +€6.05" in sol,
        sol.endswith("stop −3.0%"),
    ) == (True, True, True, True)


def test_narrow_position_row_drops_prices_and_trend() -> None:
    """Under 64 cells a row is coin, tf, change, net and the stop meter only."""
    sol = _rows_text(50)[2].split()
    assert (sol[0], sol[1], sol[-1]) == ("▸SOL", "1d", "−3.0%")


def test_stop_room_is_amber_when_the_stop_is_close() -> None:
    """SOL is 1.4 ATR from its stop: its meter is amber. ETH at 3.1 ATR is not."""
    model = _model()
    rows = model.positions()
    group = position_rows(rows, {}, _ctx(), BASE, width=100, selected=None)
    eth, sol = group.renderables[1], group.renderables[2]
    assert (
        BASE.warn in {str(s.style) for s in sol.spans},  # type: ignore[union-attr]
        BASE.warn in {str(s.style) for s in eth.spans},  # type: ignore[union-attr]
    ) == (True, False)


def test_blind_positions_fall_back_to_the_money_free_table() -> None:
    """On a blind surface the rows carry no euro amount and no percentage."""
    model = _model()
    group = position_rows(model.positions(), {}, _ctx(Surface.SLACK), BASE, width=100)
    text = _plain(group)
    assert ("€" in text, "%" in text, "ETH" in text) == (False, False, True)


def test_empty_state_names_the_coins_and_the_next_look() -> None:
    """'hermes is watching BTC ETH SOL and will look again at 18:00' (16:00 UTC in Madrid)."""
    text = empty_positions(
        ("BTC", "ETH", "SOL"), datetime(2026, 10, 3, 16, tzinfo=UTC), _ctx(), BASE, frame=0
    )
    lines = text.plain.splitlines()
    assert (lines[0], lines[2]) == (
        "⠁ No open positions",
        "hermes is watching BTC ETH SOL and will look again at 18:00",
    )


def test_radar_frame_steps_and_the_line_stands_alone_without_a_time() -> None:
    """Frame 3 draws the fourth radar dot; with no next time only the first line shows."""
    assert empty_positions((), None, _ctx(), BASE, frame=3).plain == "⡀ No open positions"


# ----------------------------------------------------------------------
# Log tail and 90-day line
# ----------------------------------------------------------------------


_LINE = LogLine(
    at=datetime(2026, 10, 3, 12, 0, 5, tzinfo=UTC),
    level="info",
    event="serve.bar.processed",
    fields=(("pair", "BTC/USDT"),),
)


def test_log_tail_explains_demo_and_missing_journal() -> None:
    """Demo data has no journal; a machine without journalctl says where to look."""
    assert (
        _plain(log_tail([_LINE], _ctx(), demo=True, count=5)).strip(),
        _plain(log_tail(None, _ctx(), demo=False, count=5)).strip(),
    ) == (LOGS_DEMO, LOGS_UNAVAILABLE)


def test_log_tail_keeps_the_last_lines() -> None:
    """Three lines asked for two: the last two show, in Madrid time."""
    later = replace(_LINE, at=datetime(2026, 10, 3, 12, 1, 5, tzinfo=UTC), event="feed.retry")
    text = _plain(log_tail([_LINE, _LINE, later], _ctx(), demo=False, count=2))
    assert [line.split()[0] for line in text.splitlines()] == ["14:00:05", "14:01:05"]


def test_run_line_ends_with_the_bar_and_percentage() -> None:
    """Day 12 of 90 is 13%; the words lead and the bar fills its share of the cells."""
    test = ShadowTest(status=ShadowTestStatus.ON_TRACK, day=12, length_days=90)
    line = run_line(test, BASE, width=80).plain
    assert (
        line.startswith("Shadow test · day 12 of 90 · on track  "),
        line.endswith("  13%"),
        line.count("█"),
    ) == (True, True, 5)


def test_run_bar_fills_day_over_length_in_the_brand_gradient() -> None:
    """Day 45 of 90 over 35 cells: 18 full (17.5 rounds to even), the first the first brand stop."""
    test = ShadowTest(status=ShadowTestStatus.ON_TRACK, day=45, length_days=90)
    line = run_line(test, BASE, width=80)
    assert (line.plain[39:74], line.spans[0].start, line.spans[0].style) == (
        "█" * 18 + "░" * 17,
        39,
        "#8BE9FF",
    )


def test_run_line_before_the_start_is_the_words_only() -> None:
    """A test that has not started has no bar."""
    test = ShadowTest(status=ShadowTestStatus.NOT_STARTED, day=None, note="waits on #353")
    assert run_line(test, BASE, width=80).plain == "Shadow test · not started · waits on #353"
