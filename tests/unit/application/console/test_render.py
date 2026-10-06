"""Behaviour of the shared console renderers.

Each test protects one rule of the approved console UX:

- demo data always carries a DEMO DATA tag, on every screen;
- blind surfaces never show money, percentages or close prices;
- the heartbeat is quiet when OK, amber when late and a red row when down;
- when hermes is down, money reads "as of" the stop time;
- a pending tax reads "tax estimate: not available yet"; a loss is a credit;
- the home overview shows at most four activity rows.
"""

# The console prints the typographic minus (U+2212) and times sign on purpose.
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import replace

import pytest
from rich.text import Text

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Surface
from hermes.application.console.render import (
    RenderContext,
    render_activity,
    render_heartbeat,
    render_positions,
    render_tax_footnote,
    to_plain_text,
)
from tests.unit.application.console._screens import SCENARIOS, SCREENS, render

_BLIND = tuple(
    s
    for s in Surface
    if s not in {Surface.OPERATOR_CLI, Surface.OPERATOR_TUI, Surface.TELEGRAM_PULLED}
)


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("screen", SCREENS)
def test_every_demo_screen_shows_demo_tag(scenario: str, screen: str) -> None:
    """Every screen rendered from a fixture scenario carries the DEMO DATA tag."""
    text = render(scenario, screen, surface=Surface.OPERATOR_CLI)
    assert text.startswith(f" DEMO DATA · scenario {scenario}")


@pytest.mark.parametrize("screen", SCREENS)
def test_real_data_screen_has_no_demo_tag(screen: str) -> None:
    """A context without a demo scenario renders no DEMO DATA tag."""
    model = FixtureReadModel.load("ok-two-positions")
    ctx = replace(RenderContext.for_model(model, surface=Surface.OPERATOR_CLI), demo_scenario=None)
    assert "DEMO DATA" not in to_plain_text(SCREENS[screen](model, ctx), width=80)


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("screen", SCREENS)
def test_blind_surface_shows_no_money(surface: Surface, scenario: str, screen: str) -> None:
    """No euro amount and no percentage appear on a blind surface, on any screen."""
    text = render(scenario, screen, surface=surface)
    assert ("€" in text, "%" in text) == (False, False)


def test_blind_activity_omits_close_price() -> None:
    """A close price next to the entry would leak P&L, so blind rows drop it."""
    text = render("ok-two-positions", "activity", surface=Surface.SLACK)
    assert "closed BTC\n" in text


def test_blind_positions_show_only_the_allowed_columns() -> None:
    """Blind position rows carry coin, side, opened at, entry, size and stop only."""
    text = render("ok-two-positions", "positions", surface=Surface.TELEGRAM_PUSHED)
    header = next(line for line in text.splitlines() if line.startswith("coin"))
    assert header.split() == ["coin", "side", "opened", "entry", "size", "stop"]


def test_ok_heartbeat_is_one_quiet_line() -> None:
    """When all is well the heartbeat is a single live line with both check times."""
    text = render("ok-two-positions", "heartbeat", surface=Surface.OPERATOR_CLI)
    assert text.splitlines()[1].strip() == "● live · checked 14:00 · next 18:00"


def test_ok_heartbeat_dot_carries_the_live_style_and_the_words_stay_dim() -> None:
    """The OK dot uses ``ctx.live_dot``, not dim; only the words are dim (#711, "live is gray")."""
    model = FixtureReadModel.load("ok-two-positions")
    ctx = replace(
        RenderContext.for_model(model, surface=Surface.OPERATOR_TUI),
        demo_scenario=None,
        live_dot="bold #00F0FF",
    )
    line = render_heartbeat(model.heartbeat(), ctx).renderables[0]
    assert isinstance(line, Text)
    spans = [
        (line.plain[span.start : span.end].strip()[:6], str(span.style)) for span in line.spans
    ]
    assert spans == [("●", "bold #00F0FF"), ("live ·", "dim")]


def test_late_heartbeat_names_the_missed_check_and_delay() -> None:
    """A late check shows which check is late and by how long."""
    text = render("late", "heartbeat", surface=Surface.OPERATOR_CLI)
    assert text.splitlines()[1].strip() == "◐ 18:00 check late · 12m"


def test_down_heartbeat_is_the_call_to_action() -> None:
    """Down shows since when and where to look, nothing else."""
    text = render("down-stale", "heartbeat", surface=Surface.OPERATOR_CLI)
    assert text.splitlines()[1] == "✖ hermes down since 18:04 · run hermes logs for why"


def test_tui_down_hint_can_name_its_key() -> None:
    """The TUI replaces the hint with its own key binding."""
    model = FixtureReadModel.load("down-stale")
    ctx = RenderContext.for_model(model, surface=Surface.OPERATOR_TUI, down_hint="press L for why")
    text = to_plain_text(SCREENS["heartbeat"](model, ctx), width=80)
    assert "✖ hermes down since 18:04 · press L for why" in text


def test_down_marks_hero_as_of_stop_time() -> None:
    """While down, the hero says how old the numbers are."""
    text = render("down-stale", "portfolio", surface=Surface.OPERATOR_CLI)
    assert "€1,021.06   ▲ +€12.30 +1.22% today · as of 18:04" in text


def test_live_hero_has_no_as_of_note() -> None:
    """Live numbers carry no staleness note."""
    text = render("ok-two-positions", "portfolio", surface=Surface.OPERATOR_CLI)
    assert "as of" not in text


def test_hero_change_follows_the_range() -> None:
    """The year range labels its change 'this year'."""
    text = render("ok-two-positions", "portfolio-year", surface=Surface.OPERATOR_CLI)
    assert "▲ +€21.06 +2.11% this year" in text


def test_pending_tax_reads_not_available_yet() -> None:
    """Before the tax module lands, the portfolio says so instead of showing zero tax."""
    text = render("tax-pending", "portfolio", surface=Surface.OPERATOR_CLI)
    assert "Open ▲ +€9.36 · tax estimate: not available yet" in text


def test_pending_tax_footnote_says_net_is_before_tax() -> None:
    """The help-screen footnote of a pending tax says the net is before tax."""
    summary = FixtureReadModel.load("tax-pending").portfolio()
    assert (
        render_tax_footnote(summary).plain == "tax estimate: not available yet · net is before tax"
    )


def test_pending_tax_detail_row_reads_not_available_yet() -> None:
    """The position detail's tax row reads the pending wording."""
    text = render("tax-pending", "position-detail", surface=Surface.OPERATOR_CLI)
    tax_row = next(line for line in text.splitlines() if "Est. tax" in line)
    assert tax_row.endswith("tax estimate: not available yet")


def test_losing_year_shows_tax_as_credit() -> None:
    """A loss shows the tax line as a positive credit, not €0."""
    text = render("losing-year", "portfolio", surface=Surface.OPERATOR_CLI)
    tax_row = next(line for line in text.splitlines() if "Est. tax" in line)
    assert tax_row.split("tax", 1)[1].split() == ["▲", "+€0.61", "credit", "▲", "+€9.27", "credit"]


def test_waterfall_net_row_is_gross_minus_fees_minus_tax() -> None:
    """The year Net cell shows 14.80 - 3.10 - 2.22 = 9.48."""
    text = render("ok-two-positions", "portfolio", surface=Surface.OPERATOR_CLI)
    net_row = next(line for line in text.splitlines() if line.startswith("Net"))
    assert net_row.split()[-2:] == ["▲", "+€9.48"]


def test_footnote_names_band_and_room() -> None:
    """The tax footnote names the method, the band and the room to the next band."""
    summary = FixtureReadModel.load("ok-two-positions").portfolio()
    assert (
        render_tax_footnote(summary).plain == "est. · FIFO · 19% band · €5,988.30 left before 21%"
    )


def test_portfolio_has_no_tax_explanation() -> None:
    """The home portfolio block keeps only the waterfall numbers, no tax footnote."""
    text = render("ok-two-positions", "portfolio", surface=Surface.OPERATOR_CLI)
    assert ("FIFO" in text, "band" in text) == (False, False)


def test_position_row_shows_net_only() -> None:
    """A position row shows net money, labelled, and no gross figure."""
    text = render("ok-two-positions", "positions", surface=Surface.OPERATOR_CLI)
    eth = next(line for line in text.splitlines() if line.startswith("ETH"))
    assert "▲ +€1.54 net" in eth
    assert "gross" not in text.lower()


def test_stop_column_is_price_and_distance_in_eur() -> None:
    """The stop column reads the EUR stop price and the distance in percent, no ATR wording."""
    text = render("ok-two-positions", "positions", surface=Surface.OPERATOR_CLI, width=120)
    eth = next(line for line in text.splitlines() if line.startswith("ETH"))
    assert (eth.endswith("€2,210 · −3.4%"), "ATR" in text) == (True, False)


def test_narrow_positions_omit_stop_columns() -> None:
    """At 80 columns the wide-only columns stay out."""
    text = render("ok-two-positions", "positions", surface=Surface.OPERATOR_CLI)
    assert ("to stop" in text, "cushion" in text) == (False, False)


def test_empty_positions_state() -> None:
    """With no positions the screen says so."""
    text = render("empty", "positions", surface=Surface.OPERATOR_CLI)
    assert text.splitlines()[1] == "No open shadow positions."


def test_detail_money_lines_add_up() -> None:
    """The detail shows gross, both fees, tax and a net equal to their difference."""
    text = render("ok-two-positions", "position-detail", surface=Surface.OPERATOR_CLI)
    all_lines = text.splitlines()
    start = all_lines.index("Money if sold now") + 1
    rows = [line.split("  ")[0] + " " + line.split()[-1] for line in all_lines[start : start + 5]]
    assert rows == [
        "Gross +€2.10",
        "− Fees paid −€0.08",
        "− Fees to close (est.) −€0.09",
        "− Est. tax if sold now −€0.39",
        "Net +€1.54",
    ]


def test_missing_position_detail_says_so() -> None:
    """Asking for a coin with no open position renders a plain message."""
    text = render("empty", "position-detail", surface=Surface.OPERATOR_CLI)
    assert text.splitlines()[1] == "No open position for that coin."


def test_activity_groups_by_day_with_relative_times_today() -> None:
    """Today's rows use relative times; older days use the clock and a day name."""
    text = render("ok-two-positions", "activity", surface=Surface.OPERATOR_CLI)
    lines = [line.strip() for line in text.splitlines()[1:]]
    assert lines[:4] == [
        "Today",
        "2h ago      closed BTC at €52,464 · ▲ +€3.18 net",
        "4h ago      SOL stop raised ×6, €119.1→€121.7",
        "Yesterday",
    ]


def test_activity_names_older_days() -> None:
    """Days before yesterday show a weekday and date."""
    text = render("ok-two-positions", "activity", surface=Surface.OPERATOR_CLI)
    assert "Mon 28 Sep" in text.splitlines()


def test_activity_without_rows_says_so() -> None:
    """An empty feed renders a plain message."""
    text = render("empty", "activity", surface=Surface.OPERATOR_CLI)
    assert text.splitlines()[1] == "No activity yet."


def test_activity_limit_caps_rows_across_days() -> None:
    """A limit of three keeps two rows of today and one of yesterday."""
    model = FixtureReadModel.load("ok-two-positions")
    ctx = RenderContext.for_model(model, surface=Surface.OPERATOR_CLI)
    text = to_plain_text(render_activity(model.activity(), ctx, limit=3), width=80)
    assert [line.split("  ")[0] for line in text.splitlines()[1:]] == [
        "Today",
        "2h ago",
        "4h ago",
        "Yesterday",
        "15:00",
    ]


def test_overview_shows_one_demo_tag() -> None:
    """The overview is one screen, so it carries the tag once."""
    text = render("ok-two-positions", "overview", surface=Surface.OPERATOR_CLI)
    assert text.count("DEMO DATA") == 1


def test_shadow_test_line() -> None:
    """The 90-day line reads day N of 90 and the verdict."""
    text = render("ok-two-positions", "shadow-test", surface=Surface.OPERATOR_CLI)
    assert text.splitlines()[1] == "Shadow test · day 12 of 90 · on track"


def test_shadow_test_not_started_names_what_it_waits_on() -> None:
    """Before it starts, the line says what it waits on."""
    text = render("empty", "shadow-test", surface=Surface.OPERATOR_CLI)
    assert text.splitlines()[1] == "Shadow test · not started · waits on #353"


def test_plain_text_has_no_trailing_spaces() -> None:
    """Plain output never ends a line with spaces, so snapshots stay stable."""
    model = FixtureReadModel.load("losing-year")
    ctx = RenderContext.for_model(model, surface=Surface.OPERATOR_CLI)
    text = to_plain_text(render_positions(model.positions(), ctx), width=80)
    assert [line for line in text.splitlines() if line != line.rstrip()] == []
