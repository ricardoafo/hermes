"""The Logs screen of the Cockpit console (issue #716).

Invariants:

- screen 8 reads the journal once when shown and lists every line, newest
  first, through the shared query list; the spec query
  ``level>=warn service:engine coin:ETH since:7d`` keeps the one ETH retry;
- Tab in the query line completes a field instead of leaving the screen;
- facet counts follow the query;
- the volume histogram stacks each bar by level, worst at the bottom, in the
  token colours; ``D`` ``W`` ``M`` ``Y`` zoom its window;
- Enter shows the selected line as pretty JSON: a DetailPane when wide, a
  DetailScreen on compact terminals;
- ``T`` opens the trace (``◂ Logs › trace H1M9XA``) and back puts the cursor
  on that trace's line; a line with no trace says so;
- ``F`` follows: each refresh re-reads and appends the new lines; without
  ``F`` nothing is re-read;
- ``C`` copies the line's JSON; ``E`` writes it under the exports folder,
  which ``export_dir`` puts under the home folder on the PC and refuses in
  an ssh session (``--vps``);
- demo data and a missing journal say so instead of reading.

Every app gets a frozen clock (2026-10-03 12:12 UTC) and motion off; the
journal is the fixture export tests/fixtures/journal/logs_screen.jsonl.
"""

# ruff: noqa: RUF001, RUF002 - the single right angle quote is the design spec's breadcrumb separator

import asyncio
import io
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, cast

import pytest
from rich.console import Console
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import ConsoleReadModel
from hermes.application.console.log_figures import VolumeBucket, log_line_pretty_json
from hermes.application.console.logs import LogLine, parse_journal_entry
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_drilldown import DetailPane, DetailScreen
from hermes.entrypoints.tui_logs import (
    READ_LINES,
    LogsView,
    export_dir,
    export_line,
    histogram_text,
    level_colour,
    trace_text,
)
from hermes.entrypoints.tui_query import FacetFilter, QueryBar, QueryInput
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import BASE

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_FIXTURE = Path(__file__).parents[2] / "fixtures" / "journal" / "logs_screen.jsonl"
_TRACE_A = "01J9Z8T3M5BQR2FVKN4XK7Q2ZP"


def _lines() -> list[LogLine]:
    raw = _FIXTURE.read_text(encoding="utf-8").splitlines()
    return [line for line in map(parse_journal_entry, raw) if line is not None]


class _LiveModel:
    """A fixture model that claims to be live (no demo tag), so the journal is read."""

    def __init__(self) -> None:
        self._inner = FixtureReadModel.load("ok-two-positions")

    @property
    def demo_scenario(self) -> None:
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _Journal:
    """Serves the fixture lines (or ``None``) and records every read."""

    def __init__(self, lines: Sequence[LogLine] | None) -> None:
        self.lines = None if lines is None else list(lines)
        self.asked: list[int] = []

    def __call__(self, count: int) -> list[LogLine] | None:
        self.asked.append(count)
        return None if self.lines is None else self.lines[-count:]


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _screen_text(app: HomeApp) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


async def _press(pilot: Pilot[None], keys: Sequence[str]) -> None:
    for key in keys:
        await pilot.press(key)
        await pilot.pause()


def _logs[T](
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    size: tuple[int, int] = (80, 24),
    journal: _Journal | None = None,
    live: bool = True,
    exports: Path | None = None,
    refresh_seconds: float = 3600,
    theme: ThemeChoice | None = None,
) -> T:
    model: ConsoleReadModel = (
        cast("ConsoleReadModel", _LiveModel())
        if live
        else FixtureReadModel.load("ok-two-positions")
    )

    async def drive() -> T:
        app = HomeApp(
            model,
            journal=journal if journal is not None else _Journal(_lines()),
            clock=lambda: _FROZEN,
            motion=False,
            refresh_seconds=refresh_seconds,
            exports=exports,
            theme=theme,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await _press(pilot, ["8"])
            return await probe(app, pilot)

    return asyncio.run(drive())


def _status(app: HomeApp) -> str:
    return app.logs_view.query_one(QueryBar).status_text


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def test_export_dir_is_under_home_on_the_pc(tmp_path: Path) -> None:
    """Without an ssh session the export folder is ~/hermes-exports."""
    assert export_dir({}, tmp_path) == tmp_path / "hermes-exports"


@pytest.mark.parametrize("name", ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
def test_export_dir_is_refused_over_ssh(tmp_path: Path, name: str) -> None:
    """In an ssh session (hermes --vps tui) a file would land on the VPS: no folder."""
    ssh = {name: "100.64.0.1 50000 100.64.0.2 22"}
    assert (export_dir(ssh, tmp_path), export_dir({**ssh, name: ""}, tmp_path)) == (
        None,
        tmp_path / "hermes-exports",
    )


def test_export_line_writes_the_pretty_json(tmp_path: Path) -> None:
    """The line lands as log-<UTC time>-<trace tail>.json holding its pretty JSON."""
    line = _lines()[6]
    path = export_line(line, tmp_path / "hermes-exports")
    assert (path.relative_to(tmp_path).as_posix(), path.read_text(encoding="utf-8")) == (
        "hermes-exports/log-20261003-080006-K7Q2ZP.json",
        log_line_pretty_json(line) + "\n",
    )


def test_export_line_without_a_trace_is_named_line(tmp_path: Path) -> None:
    """A line with no correlation id ends its file name with "line"."""
    assert export_line(_lines()[2], tmp_path).name == "log-20261002-200002-line.json"


def test_export_line_keeps_only_letters_and_digits_of_the_trace_tail(tmp_path: Path) -> None:
    """An id holding path separators cannot name a file outside the export folder."""
    line = replace(_lines()[6], correlation_id="01J9Z8T3M5BQR2FVKN4X../..\\/a")
    path = export_line(line, tmp_path / "out")
    assert (path.parent, path.name) == (tmp_path / "out", "log-20261003-080006-a.json")


def test_level_colours_come_from_the_tokens() -> None:
    """The levels read: debug dim, info muted, warning amber, error and critical the down colour."""
    assert [level_colour(i, BASE) for i in range(5)] == [
        BASE.dim,
        BASE.muted,
        BASE.warn,
        BASE.down,
        BASE.down,
    ]


def _spans(text_rows: str, styles: list[tuple[int, str]]) -> list[tuple[str, str]]:
    return [(text_rows[start], style) for start, style in styles]


def test_histogram_bars_scale_to_the_peak() -> None:
    """One row: empty, a full info bar (peak 8) and a half error bar (4)."""
    buckets = [
        VolumeBucket(_FROZEN, (0, 0, 0, 0, 0)),
        VolumeBucket(_FROZEN, (0, 8, 0, 0, 0)),
        VolumeBucket(_FROZEN, (0, 0, 0, 4, 0)),
    ]
    text = histogram_text(buckets, BASE, height=1, window="24 h")
    rows = text.plain.splitlines()
    colours = [(s.start, str(s.style)) for s in text.spans if s.end == s.start + 1][:2]
    assert (rows[0], _spans(text.plain, colours)) == (
        " █▄",
        [("█", BASE.muted), ("▄", BASE.down)],
    )
    assert rows[1] == "24 h · peak 8 per bar  ■ debug ■ info ■ warn ■ error"


def test_histogram_stacks_the_worst_level_at_the_bottom() -> None:
    """One error and one info over two rows: the error fills the bottom cell, info the top."""
    text = histogram_text([VolumeBucket(_FROZEN, (0, 1, 0, 1, 0))], BASE, height=2, window="24 h")
    bars = [(text.plain[s.start], str(s.style)) for s in text.spans if s.start < 4]
    assert (text.plain.splitlines()[:2], bars) == (
        ["█", "█"],
        [("█", BASE.muted), ("█", BASE.down)],
    )


def test_trace_text_is_a_tree_with_timings() -> None:
    """The 08:00 trace reads as a tree: root, namespaces with spans, lines with gaps."""
    console = Console(width=100, file=io.StringIO(), record=True, color_system=None)
    console.print(trace_text(_lines(), _TRACE_A, UTC, BASE))
    lines = [line.rstrip() for line in console.export_text().splitlines()]
    assert lines[:5] == [
        "trace K7Q2ZP · 5 lines · 2.400 s · started 2026-10-03 08:00:05",
        "01J9Z8T3M5BQR2FVKN4XK7Q2ZP",
        "├── serve  +0.000 s · span 2.400 s",
        "│   ├── +0.000 s  (+0.000 s)  INFO     serve.bar.started  pair=BTC/USDT interval=4h",
        "│   └── +2.400 s  (+1.300 s)  INFO     serve.bar.processed  pair=BTC/USDT "
        "cash_eur=4456.10",
    ]
    assert lines[-2:] == [
        "└── feed  +1.100 s · span 0.000 s",
        "    └── +1.100 s  (+0.200 s)  WARNING  feed.retry  pair=ETH/USDT attempt=2",
    ]


# ----------------------------------------------------------------------
# The screen
# ----------------------------------------------------------------------


def test_screen_eight_reads_the_journal_once_and_lists_every_line() -> None:
    """Showing Logs reads the last 2000 lines once; all 12 are listed, newest first."""
    journal = _Journal(_lines())

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str, list[int]]:
        del pilot
        first = app.logs_view.table.get_row_at(0)
        return _status(app), f"{first[0]} {first[3]}", journal.asked

    assert _logs(probe, journal=journal) == (
        "12 of 12 · sort time ▼",
        "10-03 14:00:09 serve.bar.processed",
        [READ_LINES],
    )


def test_the_spec_query_keeps_the_eth_retry() -> None:
    """Typing the spec's query leaves one line: the 10:00:06 ETH feed retry."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        await _press(pilot, ["slash"])
        await pilot.press(*"level>=warn service:engine coin:ETH since:7d")
        await pilot.pause()
        row = app.logs_view.table.get_row_at(0)
        return _status(app), f"{row[0]} {row[1]} {row[3]} {row[4]}"

    assert _logs(probe) == ("1 of 12 · sort time ▼", "10-03 10:00:06 warning feed.retry ETH")


def test_facet_counts_follow_the_query() -> None:
    """After ``level>=warn`` the facets count the three warnings and errors only."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, ["slash"])
        await pilot.press(*"level>=warn")
        await pilot.pause()
        return app.logs_view.query_one(FacetFilter).counts_text

    assert _logs(probe) == ("level  warning 2 · error 1\nservice  engine 2 · bot 1\ncoin  ETH 1")


def test_tab_completes_a_field_in_the_query_line() -> None:
    """``lev`` + Tab becomes ``level:`` and the console stays on Logs."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, int]:
        await _press(pilot, ["slash", "l", "e", "v", "tab"])
        return app.logs_view.query_one(QueryInput).value, app.tab

    assert _logs(probe) == ("level:", 7)


def test_tab_outside_the_query_line_still_cycles_screens() -> None:
    """With the table focused, Tab wraps from Logs to Home."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> int:
        await _press(pilot, ["tab"])
        return app.tab

    assert _logs(probe) == 0


def test_escape_leaves_the_query_line_for_the_table() -> None:
    """Esc in the query line moves to the table and keeps the Logs screen."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int]:
        await _press(pilot, ["slash", "escape"])
        return app.focused is app.logs_view.table, app.tab

    assert _logs(probe) == (True, 7)


def test_zoom_keys_set_the_histogram_window() -> None:
    """W zooms to 7 days, Y to 365 days; the caption names the window."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        captions = []
        for key in ("w", "y", "d"):
            await _press(pilot, [key])
            histogram = app.logs_view.query_one("#logs-histogram")
            captions.append(str(histogram.render()).splitlines()[-1].split(" · ")[0])
        return captions

    assert _logs(probe, size=(160, 45)) == ["7 days", "365 days", "24 h"]


def test_the_week_window_peak_is_the_busiest_cycle() -> None:
    """Over 7 days (bars of about an hour) the busiest bar is the five-line 08:00 cycle."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, ["w"])
        histogram = app.logs_view.query_one("#logs-histogram")
        return str(histogram.render()).splitlines()[-1].split("  ")[0]

    assert _logs(probe, size=(160, 45)) == "7 days · peak 5 per bar"


def test_enter_opens_the_line_as_json_in_a_pane_when_wide() -> None:
    """At 160 columns Enter opens the DetailPane holding the selected line's JSON."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int, bool]:
        await _press(pilot, ["down", "enter"])
        text = _screen_text(app)
        return (
            app.logs_view.query_one(DetailPane).is_open,
            len(app.screen_stack),
            '"event": "serve.notify.failed",' in text,
        )

    assert _logs(probe, size=(160, 45)) == (True, 1, True)


def test_the_pane_follows_the_cursor_and_escape_closes_it() -> None:
    """Moving down updates the pane; Esc closes it and stays on Logs."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool, int]:
        await _press(pilot, ["enter", "down", "down", "down"])
        moved = '"event": "bot.command.slow",' in _screen_text(app)
        await _press(pilot, ["escape"])
        return moved, app.logs_view.query_one(DetailPane).is_open, app.tab

    assert _logs(probe, size=(160, 45)) == (True, False, 7)


def test_enter_opens_a_detail_screen_on_compact_terminals() -> None:
    """At 80 columns Enter opens the line as a DetailScreen with the Logs breadcrumb."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str]:
        await _press(pilot, ["enter"])
        return isinstance(app.screen, DetailScreen), _screen_text(app).splitlines()[0].strip()

    assert _logs(probe) == (True, "◂ Logs › line 14:00:09")


def test_t_opens_the_trace_and_back_keeps_the_cursor_on_it() -> None:
    """T opens trace H1M9XA; ] moves to K7Q2ZP; back puts the cursor on its newest line."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str, int, str]:
        await _press(pilot, ["t"])
        first = _screen_text(app).splitlines()[0].strip()
        await _press(pilot, ["right_square_bracket"])
        second = _screen_text(app).splitlines()[0].strip()
        await _press(pilot, ["escape"])
        row = app.logs_view.table.cursor_row
        return first, second, row, str(app.logs_view.table.get_row_at(row)[3])

    assert _logs(probe) == (
        "◂ Logs › trace H1M9XA",
        "◂ Logs › trace K7Q2ZP",
        4,
        "serve.bar.processed",
    )


def test_t_on_a_line_without_a_trace_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bot warning has no correlation id: no screen opens and a warning says why."""
    said: list[str] = []

    async def probe(app: HomeApp, pilot: Pilot[None]) -> int:
        monkeypatch.setattr(app, "notify", lambda message, **_: said.append(message))
        await _press(pilot, ["down", "down", "down", "t"])
        return len(app.screen_stack)

    assert (_logs(probe), said) == (1, ["This line has no trace (no correlation id)."])


def test_follow_appends_new_lines_on_each_refresh() -> None:
    """F then a new journal line: the next tick appends it and the list grows to 13."""
    journal = _Journal(_lines())
    extra = LogLine(
        at=_FROZEN,
        level="error",
        event="serve.bar.failed",
        correlation_id="01J9Z8T3M5BQR2FVKN4X0000AA",
    )

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str, str]:
        await _press(pilot, ["f"])
        assert journal.lines is not None
        journal.lines.append(extra)
        app.logs_view.tick()
        await pilot.pause()
        row = app.logs_view.table.get_row_at(0)
        return _status(app), str(row[3]), app.logs_view.status_text

    assert _logs(probe, journal=journal) == (
        "13 of 13 · sort time ▼",
        "serve.bar.failed",
        "13 lines read · following · re-read every 5 s",
    )


def test_the_refresh_timer_drives_follow() -> None:
    """With F on and a short refresh, the app's own timer re-reads and appends a new line."""
    journal = _Journal(_lines())

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, ["f"])
        assert journal.lines is not None
        journal.lines.append(LogLine(at=_FROZEN, level="info", event="serve.bar.started"))
        await pilot.pause(0.5)
        return _status(app)

    assert _logs(probe, journal=journal, refresh_seconds=0.05) == "13 of 13 · sort time ▼"


def test_without_follow_the_refresh_reads_nothing() -> None:
    """Follow off: refreshes leave the journal alone after the first read."""
    journal = _Journal(_lines())

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[int]:
        app.logs_view.tick()
        await pilot.pause(0.3)
        return journal.asked

    assert _logs(probe, journal=journal, refresh_seconds=0.05) == [READ_LINES]


def test_c_copies_the_line_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """C puts the selected line's pretty JSON on the clipboard."""
    copied: list[str] = []

    async def probe(app: HomeApp, pilot: Pilot[None]) -> None:
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        await _press(pilot, ["down", "c"])

    _logs(probe)
    assert json.loads(copied[0])["event"] == "serve.notify.failed"
    assert copied == [log_line_pretty_json(_lines()[10])]


def test_e_exports_under_the_exports_folder(tmp_path: Path) -> None:
    """E writes the selected line's JSON under the exports folder (here under tmp_path)."""
    folder = export_dir({}, tmp_path)

    async def probe(app: HomeApp, pilot: Pilot[None]) -> None:
        del app
        await _press(pilot, ["e"])

    _logs(probe, exports=folder)
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.json"))
    assert written == ["hermes-exports/log-20261003-120009-H1M9XA.json"]


def test_e_over_vps_says_export_works_from_the_pc(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no exports folder (--vps) E says to export from the PC instead."""
    said: list[str] = []

    async def probe(app: HomeApp, pilot: Pilot[None]) -> None:
        monkeypatch.setattr(app, "notify", lambda message, **_: said.append(message))
        await _press(pilot, ["e"])

    _logs(probe, exports=None)
    assert said == ["Export works from the PC; over --vps, copy the line with C."]


def test_demo_data_reads_no_journal() -> None:
    """Demo scenarios say the logs come from the VPS and never call the journal."""
    journal = _Journal(_lines())

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        del pilot
        return app.logs_view.status_text, _status(app)

    assert (_logs(probe, journal=journal, live=False), journal.asked) == (
        ("Logs come from the VPS journal; demo data has none.", "0 of 0 · sort time ▼"),
        [],
    )


def test_a_missing_journal_says_where_to_look() -> None:
    """No journalctl here: the screen says to run hermes logs on the VPS."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.logs_view.status_text

    assert _logs(probe, journal=_Journal(None)) == (
        "The journal is not readable here; run hermes logs on the VPS."
    )


def test_logs_view_is_the_eighth_screen() -> None:
    """The Logs tab hosts the LogsView, not the old placeholder."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool]:
        del pilot
        return (
            isinstance(app.query_one("#tab-8").children[0], LogsView),
            "coming next · issue #716" in _screen_text(app),
        )

    assert _logs(probe) == (True, False)


# ----------------------------------------------------------------------
# Snapshots (motion off, frozen clock)
# ----------------------------------------------------------------------


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``logs_list_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)
_VIEWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("list", ()),
    ("pane", ("down", "enter")),
    ("trace", ("down", "down", "down", "down", "t")),
)


def _svg(keys: Sequence[str], size: tuple[int, int], theme: ThemeChoice) -> str:
    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, keys)
        return _repo_clean(app.export_screenshot())

    return _logs(probe, size=size, theme=theme)


@pytest.mark.parametrize(
    ("view", "keys", "theme_name", "theme", "size"),
    [
        pytest.param(view, keys, name, theme, size, id=f"logs_{view}_{name}_{size[0]}x{size[1]}")
        for view, keys in _VIEWS
        for name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_logs_svg_snapshot(
    snapshot: SnapshotAssertion,
    view: str,
    keys: tuple[str, ...],
    theme_name: str,
    theme: ThemeChoice,
    size: tuple[int, int],
) -> None:
    """The reviewed picture of one Logs view (list, JSON pane, trace) in one theme and size."""
    name = f"logs_{view}_{theme_name}_{size[0]}x{size[1]}"
    assert _svg(keys, size, theme) == snapshot(extension_class=_NamedSVG, name=name)
