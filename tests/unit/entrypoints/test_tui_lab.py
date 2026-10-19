"""The Cockpit console's Lab screen, view only (issue #1056).

Invariants:

- tab 5 lists one row per strategy id, with the stage strip, the status, the
  trials and the family's Validation touches used of 5;
- the layout follows the width: compact lists only and Enter opens the
  strategy over its results under ``◂ Lab › Backtests › <id>``; standard,
  wide and ultra show the strategy and results panels beside the list;
- thresholds always show; the trial log's figures show only when values were
  read (``show_values`` on, no AI session) and ``V`` hides and shows them;
- in an AI session no figure of the trial log reaches any screen, on any row,
  in any layout, and ``V`` does nothing;
- a run value that is not the signed base or a signed neighbour carries an
  amber ``⚠``;
- the screen is read only: walking it with every key changes no file;
- the reviewed SVG snapshots at 80x24, 120x36, 160x45 and 240x60 in Neon and
  Control room.

Every app here gets a frozen clock, motion off and synthetic files.
"""

# ruff: noqa: RUF001, RUF002 - the single right angle quote is the design spec's breadcrumb separator

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from syrupy.assertion import SnapshotAssertion
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.adapters.console.lab_files import FileLabReader
from hermes.application.console.lab import LabSnapshot, build_lab, values_allowed
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_drilldown import DetailScreen
from hermes.entrypoints.tui_lab import (
    lab_layout,
    lab_rows,
    results_text,
    stage_strip,
    strategy_text,
    view_bar,
)
from hermes.entrypoints.tui_settings import (
    ConsoleSettings,
    ThemeChoice,
    load_settings,
    save_settings,
)
from hermes.entrypoints.tui_theme import BASE
from tests.support.lab_files import FIGURES, synthetic_files, write_experiment
from tests.support.tui_views import assert_view, capture

_FROZEN = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
_MADRID = ZoneInfo("Europe/Madrid")
_LAB_KEY = "5"
# The sharpe figures of the synthetic trial log as the screen would print them.
_RENDERED = ("1.739", "0.427", "2.514", "0.909")
_ROWS_BY_RECENCY = (
    "synth-daily-ma",
    "synth-ensemble",
    "synth-pullback",
    "synth-slope",
    "synth-legacy",
    "synth-prereg-only",
)


class _Reader:
    """A reader over the synthetic files, built in memory."""

    def __init__(self, *, values: bool) -> None:
        self._values = values

    def read(self) -> LabSnapshot | None:
        return build_lab([synthetic_files()], include_values=self._values)


def _screen_text(app: HomeApp) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


async def _press(pilot: Pilot[None], keys: Sequence[str]) -> None:
    for key in keys:
        await pilot.press(key)
        await pilot.pause()


def _run[T](
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    size: tuple[int, int] = (120, 36),
    values: bool = False,
    ai_session: bool = False,
    reader: FileLabReader | _Reader | None = None,
    theme: ThemeChoice | None = None,
    settings_path: Path | None = None,
) -> T:
    async def drive() -> T:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=lambda: _FROZEN,
            motion=False,
            refresh_seconds=3600,
            theme=theme,
            settings_path=settings_path,
            lab=reader if reader is not None else _Reader(values=values),
            ai_session=ai_session,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await _press(pilot, [_LAB_KEY])
            return await probe(app, pilot)

    return asyncio.run(drive())


# ----------------------------------------------------------------------
# Pure builders
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "layout"),
    [
        (80, "-compact"),
        (99, "-compact"),
        (100, "-standard"),
        (159, "-standard"),
        (160, "-wide"),
        (219, "-wide"),
        (220, "-ultra"),
        (240, "-ultra"),
    ],
)
def test_the_layout_follows_the_width(width: int, layout: str) -> None:
    """Compact below 100 columns, standard to 159, wide to 219, ultra from 220."""
    assert lab_layout(width) == layout


def test_the_stage_strip_reads_signed_train_validation_judged() -> None:
    """The champion is ✓ ✓ ✓ ★, a Train failure ✓ ✗ · ·, an unsigned draft ○ · · ·."""
    by_id = {s.strategy_id: s for s in _Reader(values=False).read().strategies}  # type: ignore[union-attr]
    assert [
        stage_strip(by_id[i]) for i in ("synth-ensemble", "synth-slope", "synth-prereg-only")
    ] == [
        "✓ ✓ ✓ ★",
        "✓ ✗ – ✗",
        "✓ · · ·",
    ]


def test_validation_not_run_reads_not_run_in_the_results_and_not_as_a_failure() -> None:
    """A Train ``goes_on: false`` shows "Validation not run" and no Validation FAIL mark."""
    by_id = {s.strategy_id: s for s in _Reader(values=False).read().strategies}  # type: ignore[union-attr]
    skipped = results_text(by_id["synth-slope"], BASE, _MADRID, reveal=False).plain
    failed = results_text(by_id["synth-daily-ma"], BASE, _MADRID, reveal=False).plain
    assert ("Validation not run" in skipped, "Validation not run" in failed) == (True, False)


def test_rows_hold_no_figure_and_one_row_per_strategy() -> None:
    """Six rows, in last-run order data, and no figure of the trial log in any cell."""
    snapshot = _Reader(values=True).read()
    assert snapshot is not None
    rows = lab_rows(snapshot.strategies, _MADRID)
    text = repr(rows)
    assert (len(rows), [f for f in _RENDERED + FIGURES if f in text]) == (6, [])


def test_the_values_tag_names_why_values_are_hidden() -> None:
    """Shown is amber and offers V; hidden says why: an AI session, V, or nothing."""
    tags = [
        view_bar("Backtests", values=state, tokens=BASE).plain.split("    ")[-1]
        for state in ("shown", "hidden", "off", "ai")
    ]
    assert tags == [
        "● values shown · V hides",
        "○ values hidden · V shows",
        "○ values hidden",
        "○ values hidden · AI session",
    ]


def test_the_strategy_panel_marks_only_the_parameter_that_left_its_signed_values() -> None:
    """⚠ appears once, on the pullback's rsi_level, and not on the champion."""
    by_id = {s.strategy_id: s for s in _Reader(values=False).read().strategies}  # type: ignore[union-attr]
    pullback = strategy_text(by_id["synth-pullback"], BASE).plain
    ensemble = strategy_text(by_id["synth-ensemble"], BASE).plain
    assert ("⚠ differs from signed" in pullback, "⚠" in ensemble) == (True, False)


def test_the_strategy_panel_lists_brief_rules_hypothesis_and_parameters() -> None:
    """The champion's panel has its four sections, its card rules and its signed neighbours."""
    by_id = {s.strategy_id: s for s in _Reader(values=False).read().strategies}  # type: ignore[union-attr]
    text = strategy_text(by_id["synth-ensemble"], BASE).plain
    assert [heading in text for heading in ("BRIEF", "RULES", "HYPOTHESIS", "PARAMETERS")] == [
        True
    ] * 4
    assert "at least two lookbacks break out together" in text
    assert "signed 2 · neighbours 1, 3 · as run 2, 1" in text


def test_the_results_panel_lists_all_17_checks_with_thresholds_and_no_figure() -> None:
    """Every gate check shows its signed threshold; without values no figure shows."""
    by_id = {s.strategy_id: s for s in _Reader(values=False).read().strategies}  # type: ignore[union-attr]
    text = results_text(by_id["synth-ensemble"], BASE, _MADRID, reveal=False).plain
    lines = text.splitlines()
    gate = [line.split()[0] for line in lines[1:18]]
    assert gate[0] == "max_drawdown" and gate[-1] == "correlation" and len(gate) == 17
    assert "< 15%" in lines[1] and "≤ 0.05" in text
    assert [f for f in _RENDERED if f in text] == []


def test_reveal_adds_the_figures_to_the_runs_and_nothing_else() -> None:
    """With reveal the sharpe appears on each run; the gate lines are unchanged."""
    by_id = {s.strategy_id: s for s in _Reader(values=True).read().strategies}  # type: ignore[union-attr]
    hidden = results_text(by_id["synth-daily-ma"], BASE, _MADRID, reveal=False).plain
    shown = results_text(by_id["synth-daily-ma"], BASE, _MADRID, reveal=True).plain
    assert (
        "sharpe 0.909" in shown,
        "0.909" in hidden,
        shown.split("RUNS")[0] == hidden.split("RUNS")[0],
    ) == (
        True,
        False,
        True,
    )


# ----------------------------------------------------------------------
# Pilot
# ----------------------------------------------------------------------


def test_key_5_opens_the_lab_with_one_row_per_strategy() -> None:
    """The list shows six rows, newest run first, under the S T V J header."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, list[str], bool]:
        table = app.lab_view.table
        first_cells = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
        return app.tab, first_cells, "S T V J" in _screen_text(app)

    assert _run(probe) == (4, list(_ROWS_BY_RECENCY), True)


def test_the_panels_follow_the_cursor() -> None:
    """On the first row the Strategy panel names it; one down names the champion."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool]:
        first = "synth-daily-ma" in _screen_text(app) and "BRIEF" in _screen_text(app)
        await _press(pilot, ["down"])
        strategy = app.lab_view.query_one("#lab-strategy")
        return first, "synth-ensemble" in str(strategy.render())

    assert _run(probe) == (True, True)


@pytest.mark.parametrize(
    ("size", "layout", "panels"),
    [
        ((80, 24), "-compact", False),
        ((120, 36), "-standard", True),
        ((160, 45), "-wide", True),
        ((240, 60), "-ultra", True),
    ],
)
def test_each_width_class_shows_the_panels_its_design_says(
    size: tuple[int, int], layout: str, panels: bool
) -> None:
    """Compact hides the strategy and results panels; the other layouts show them."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, bool, bool]:
        view = app.lab_view
        return (
            view.layout_class,
            view.query_one("#lab-strategy-panel").display,
            view.query_one("#lab-results-panel").display,
        )

    assert _run(probe, size=size) == (layout, panels, panels)


def test_wide_puts_the_results_beside_the_list_and_strategy() -> None:
    """At 160 columns the results panel is right of the list, and the strategy is under the list."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool]:
        view = app.lab_view
        list_r = view.query_one("#lab-list-panel").region
        strat_r = view.query_one("#lab-strategy-panel").region
        res_r = view.query_one("#lab-results-panel").region
        return (
            res_r.x >= list_r.right and res_r.x >= strat_r.right,
            strat_r.y >= list_r.bottom and strat_r.x == list_r.x,
        )

    assert _run(probe, size=(160, 45)) == (True, True)


def test_ultra_puts_list_strategy_and_results_in_three_columns() -> None:
    """At 240 columns the three panels sit left to right on one row."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[int]:
        view = app.lab_view
        regions = [
            view.query_one(f"#lab-{name}-panel").region for name in ("list", "strategy", "results")
        ]
        assert regions[0].right <= regions[1].x and regions[1].right <= regions[2].x
        return [r.y for r in regions]

    ys = _run(probe, size=(240, 60))
    assert len(set(ys)) == 1


def test_enter_on_a_compact_terminal_opens_the_strategy_over_its_results() -> None:
    """At 80 columns Enter opens a DetailScreen under ◂ Lab › Backtests › id; Esc returns."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, bool, str, bool]:
        await _press(pilot, ["enter"])
        screen = app.screen
        assert isinstance(screen, DetailScreen)
        text = _screen_text(app)
        crumb = text.splitlines()[0].strip()
        strategy_above_results = text.index("PARAMETERS") < text.index("GATE")
        await _press(pilot, ["escape"])
        return (
            crumb,
            strategy_above_results,
            app.lab_view.selected().strategy_id,
            isinstance(  # type: ignore[union-attr]
                app.screen, DetailScreen
            ),
        )

    crumb, ordered, cursor, still_open = _run(probe, size=(80, 24))
    assert (crumb, ordered, cursor, still_open) == (
        "◂ Lab › Backtests › synth-daily-ma",
        True,
        "synth-daily-ma",
        False,
    )


def test_brackets_step_to_the_next_strategy_inside_the_detail() -> None:
    """] inside the compact detail shows the next strategy, and Esc leaves the cursor on it."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        await _press(pilot, ["enter", "right_square_bracket"])
        assert isinstance(app.screen, DetailScreen)
        shown = app.screen.current
        await _press(pilot, ["escape"])
        return shown, app.lab_view.selected().strategy_id  # type: ignore[union-attr]

    assert _run(probe, size=(80, 24)) == ("synth-ensemble", "synth-ensemble")


def test_enter_on_a_wider_terminal_opens_nothing() -> None:
    """From 100 columns the panels already show the strategy, so Enter pushes no screen."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> int:
        await _press(pilot, ["enter"])
        return len(app.screen_stack)

    assert _run(probe, size=(120, 36)) == 1


def test_brackets_switch_between_backtests_and_drafts() -> None:
    """] shows the Drafts note (MVP2, issue #967) and [ returns to the Backtests list."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool, bool]:
        await _press(pilot, ["right_square_bracket"])
        drafts = (
            "issue #967" in _screen_text(app) and not app.lab_view.query_one("#lab-body").display
        )
        await _press(pilot, ["left_square_bracket"])
        return drafts, app.lab_view.query_one("#lab-body").display, app.lab_view.view == "Backtests"

    assert _run(probe) == (True, True, True)


def test_without_a_reader_the_screen_says_there_are_no_experiment_files() -> None:
    """A machine with no clone gets one plain line, not an empty table."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> bool:
        return "No experiment files here" in _screen_text(app)

    async def drive() -> bool:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=lambda: _FROZEN,
            motion=False,
            refresh_seconds=3600,
            lab=None,
            ai_session=False,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await _press(pilot, [_LAB_KEY])
            return await probe(app, pilot)

    assert asyncio.run(drive()) is True


# ----------------------------------------------------------------------
# Values
# ----------------------------------------------------------------------


def test_default_render_shows_no_figure_and_says_so() -> None:
    """Values off: no sharpe on the screen, and the tag says they are hidden."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[list[str], bool]:
        text = _screen_text(app)
        return [f for f in _RENDERED if f in text], "○ values hidden" in text

    assert _run(probe, size=(160, 45)) == ([], True)


def test_v_shows_and_hides_the_figures_when_values_were_read() -> None:
    """With values read they show at first (amber tag); V hides them and V shows them again."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[tuple[bool, bool]]:
        seen = []
        for keys in ([], ["v"], ["v"]):
            await _press(pilot, keys)
            text = _screen_text(app)
            seen.append(("0.909" in text, "● values shown" in text))
        return seen

    assert _run(probe, size=(160, 45), values=True) == [(True, True), (False, False), (True, True)]


def test_v_does_nothing_when_no_values_were_read() -> None:
    """Values off in the file: V changes nothing, so a hidden screen cannot be coaxed to show."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        before = app.lab_view.values_state
        await _press(pilot, ["v"])
        return before, app.lab_view.values_state

    assert _run(probe, values=False) == ("off", "off")


# SEV-1: S11 - an AI session must never see a result value (ADR-012 D-06)
def test_an_ai_session_render_contains_none_of_the_trial_logs_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With show_values on but CLAUDECODE set, no row, panel, detail or SVG holds a figure.

    The reader is built the way ``hermes tui`` builds it, from the real settings
    file and the environment, so the chain from the file to the screen is tested,
    not only the screen.
    """
    experiments = write_experiment(tmp_path)
    settings = tmp_path / "console.toml"
    save_settings(settings, ConsoleSettings(show_values=True))
    monkeypatch.setenv("CLAUDECODE", "1")
    reader = FileLabReader(
        experiments,
        values=lambda: values_allowed(load_settings(settings).show_values, {"CLAUDECODE": "1"}),
    )

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[list[str], list[str], str]:
        found: list[str] = []
        for _ in range(len(_ROWS_BY_RECENCY)):
            await _press(pilot, ["v"])
            blob = _screen_text(app) + app.export_screenshot()
            found += [f for f in (*_RENDERED, *FIGURES) if f in blob]
            await _press(pilot, ["down"])
        return (
            found,
            [r.values for r in app.lab_view.selected().runs if r.values],
            app.lab_view.values_state,
        )  # type: ignore[union-attr]

    results = []
    for size in ((80, 24), (120, 36), (160, 45), (240, 60)):
        found, kept, state = _run(probe, size=size, reader=reader, ai_session=True)
        results.append((found, kept, state))
    assert results == [([], [], "ai")] * 4


def test_the_same_walk_finds_the_figures_when_they_are_allowed() -> None:
    """Control for the AI-session test: with values read the same search does find a figure."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> bool:
        return "0.909" in _screen_text(app)

    assert _run(probe, size=(160, 45), values=True) is True


def test_the_ai_session_check_runs_on_the_compact_detail_screen_too() -> None:
    """The detail screen over 80 columns holds no figure either in an AI session."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        await _press(pilot, ["enter"])
        text = _screen_text(app) + app.export_screenshot()
        return [f for f in _RENDERED if f in text]

    assert _run(probe, size=(80, 24), reader=_Reader(values=False), ai_session=True) == []


# ----------------------------------------------------------------------
# Read only and settings
# ----------------------------------------------------------------------


def test_walking_the_screen_with_every_key_changes_no_file(tmp_path: Path) -> None:
    """Every key the screen binds is pressed on a real tree; the tree is byte-identical after."""
    experiments = write_experiment(tmp_path)
    before = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    reader = FileLabReader(experiments, values=lambda: True)

    async def probe(app: HomeApp, pilot: Pilot[None]) -> None:
        await _press(
            pilot,
            [
                "down",
                "down",
                "enter",
                "v",
                "v",
                "right_square_bracket",
                "left_square_bracket",
                "slash",
                "escape",
                "up",
            ],
        )

    _run(probe, reader=reader, size=(80, 24))
    _run(probe, reader=reader, size=(160, 45))
    assert {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()} == before


def test_picking_a_theme_keeps_show_values(tmp_path: Path) -> None:
    """Saving the theme choice rewrites the file with show_values still on."""
    path = tmp_path / "console.toml"
    save_settings(path, ConsoleSettings(show_values=True))

    async def probe(app: HomeApp, pilot: Pilot[None]) -> None:
        app.choose_theme(ThemeChoice.CONTROL)

    _run(probe, settings_path=path)
    assert load_settings(path) == ConsoleSettings(theme=ThemeChoice.CONTROL, show_values=True)


# ----------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------


_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)
# name, keys after opening the Lab, values read
_VIEWS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("list", (), False),
    ("champion", ("down",), False),
    ("mismatch", ("down", "down"), False),
    ("values", (), True),
    ("drafts", ("right_square_bracket",), False),
)


def _svg(keys: Sequence[str], size: tuple[int, int], theme: ThemeChoice, *, values: bool) -> str:
    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, keys)
        return capture(app)

    return _run(probe, size=size, theme=theme, values=values)


@pytest.mark.parametrize(
    ("view", "keys", "values", "theme_name", "theme", "size"),
    [
        pytest.param(
            view, keys, values, name, theme, size, id=f"lab_{view}_{name}_{size[0]}x{size[1]}"
        )
        for view, keys, values in _VIEWS
        for name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_lab_svg_snapshot(
    snapshot: SnapshotAssertion,
    view: str,
    keys: tuple[str, ...],
    values: bool,
    theme_name: str,
    theme: ThemeChoice,
    size: tuple[int, int],
) -> None:
    """The reviewed picture of one Lab view in one theme and size."""
    name = f"lab_{view}_{theme_name}_{size[0]}x{size[1]}"
    assert_view(snapshot, _svg(keys, size, theme, values=values), name=name)


@pytest.mark.parametrize(
    ("theme_name", "theme"),
    [pytest.param(name, theme, id=f"lab_detail_{name}_80x24") for name, theme in _THEMES],
)
def test_lab_detail_svg_snapshot(
    snapshot: SnapshotAssertion, theme_name: str, theme: ThemeChoice
) -> None:
    """The compact detail screen (strategy over results) in both themes at 80x24."""
    name = f"lab_detail_{theme_name}_80x24"
    assert_view(snapshot, _svg(("down", "enter"), (80, 24), theme, values=False), name=name)
