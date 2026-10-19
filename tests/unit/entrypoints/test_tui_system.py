"""The Cockpit console's System screen, view only (issue #717).

Invariants:

- tab 8 shows Services (state + next timer), Releases (live first), Data and
  timing, Machine and Actions, from fake readers;
- a reader that cannot answer here makes its panel say "not available here";
  a figure hermes does not record yet says "not available yet";
- NOTHING runs from the screen: with every way to start a process patched to
  fail, walking the screen with every key starts none, and no key is bound
  to an action (bootstrap, restart, rollback show as commands to copy);
- Enter on a release opens a DetailScreen under ``◂ System › Releases ›
  <sha>`` with the deploy steps and the rollback target; ``]`` steps to the
  next release; Esc goes back with the cursor on the release shown last;
- Enter on a service opens a DetailPane beside the list when wide and a
  DetailScreen (``◂ System › Services › <name>``) on compact terminals;
- the reviewed SVG snapshots at 80x24 and 160x45 (motion off, frozen clock).

Every app here gets a frozen clock, motion off and fake readers.
"""

# ruff: noqa: RUF001, RUF002 - the single right angle quote is the design spec's breadcrumb separator

import asyncio
import io
import os
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from rich.console import Console
from syrupy.assertion import SnapshotAssertion
from textual.app import App
from textual.binding import Binding
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Heartbeat, HeartbeatState
from hermes.application.console.logs import LogLine
from hermes.application.console.system import (
    SERVICE_UNITS,
    LiveRelease,
    MachineStats,
    ReleaseEntry,
    ReleaseHistory,
    ServiceStatus,
    SystemSources,
)
from hermes.entrypoints.console import system_sources
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_drilldown import DetailPane, DetailScreen
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_system import (
    NOT_HERE,
    NOT_YET,
    actions_text,
    machine_text,
    release_detail,
    releases_text,
    service_detail,
    services_text,
    timing_text,
)
from hermes.entrypoints.tui_theme import BASE
from tests.support.svg_snapshots import NamedSVG, repo_clean

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)  # 14:12 Madrid
_MADRID = ZoneInfo("Europe/Madrid")
_LIVE = "2bc2798a1f00"
_PREVIOUS = "9f01a3c5d2e1"
_EARLIER = "77de210b4c3a"
_SYSTEM_TAB = 7


class _Services:
    """Fake service reader: hermes and the bot run, timers wait, daily failed."""

    def services(self) -> tuple[ServiceStatus, ...] | None:
        statuses = []
        for unit in SERVICE_UNITS:
            failed = unit.name == "daily"
            statuses.append(
                ServiceStatus(
                    unit,
                    installed=True,
                    active="failed" if failed else ("active" if unit.timer is None else "inactive"),
                    sub="failed" if failed else ("running" if unit.timer is None else "dead"),
                    since=datetime(2026, 10, 1, 8, tzinfo=UTC),
                    timer_active=unit.timer is not None,
                    next_run=datetime(2026, 10, 3, 16, tzinfo=UTC) if unit.timer else None,
                    last_run=datetime(2026, 10, 3, 12, tzinfo=UTC) if unit.timer else None,
                )
            )
        return tuple(statuses)


def _history() -> ReleaseHistory:
    return ReleaseHistory(
        live=LiveRelease(_LIVE, datetime(2026, 10, 2, 14, 2, tzinfo=UTC)),
        previous=_PREVIOUS,
        entries=(
            ReleaseEntry(_LIVE, datetime(2026, 10, 2, 14, tzinfo=UTC)),
            ReleaseEntry(_PREVIOUS, datetime(2026, 9, 30, 9, tzinfo=UTC)),
            ReleaseEntry(_EARLIER, None),
        ),
    )


class _Releases:
    """Fake release reader: live, previous and an unfinished install."""

    def releases(self) -> ReleaseHistory | None:
        return _history()


_STATS = MachineStats(
    cpu_load=0.42,
    memory_used=0.61,
    disk_used=0.9,
    disk_free_bytes=4_200_000_000,
    ledger_bytes=12_300_000,
    last_snapshot=f"pre-{_LIVE}.sqlite3",
    last_snapshot_at=datetime(2026, 10, 2, 13, 58, tzinfo=UTC),
)


class _Machine:
    """Fake machine reader."""

    def machine(self) -> MachineStats:
        return _STATS


class _BarTiming:
    """Fake bar timing: seven checks, the slowest 9 s after the close."""

    def bar_check_seconds(self, limit: int) -> tuple[Decimal, ...]:
        return tuple(Decimal(s) for s in (4, 5, 3, 9, 4, 4, 6))[-limit:]


class _Journal:
    """Fake unit journal: records which unit was read, returns no lines."""

    def __init__(self) -> None:
        self.units: list[str] = []

    def __call__(self, unit: str, count: int) -> Sequence[LogLine] | None:
        self.units.append(unit)
        return []


def _sources(journal: _Journal | None = None) -> SystemSources:
    return SystemSources(
        services=_Services(),
        releases=_Releases(),
        machine=_Machine(),
        bar_timing=_BarTiming(),
        unit_journal=journal or _Journal(),
    )


def _screen_text(app: App[None]) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


async def _press(pilot: Pilot[None], keys: Sequence[str]) -> None:
    for key in keys:
        await pilot.press(key)
        await pilot.pause()


def _run[T](
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    size: tuple[int, int] = (160, 45),
    system: SystemSources | None = None,
    use_default: bool = False,
    theme: ThemeChoice | None = None,
) -> T:
    async def drive() -> T:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=lambda: _FROZEN,
            motion=False,
            refresh_seconds=3600,
            theme=theme,
            system=None if use_default else (system or _sources()),
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await _press(pilot, ["8"])
            return await probe(app, pilot)

    return asyncio.run(drive())


# ----------------------------------------------------------------------
# Pure builders
# ----------------------------------------------------------------------


def test_services_rows_show_state_and_next_timer_run() -> None:
    """One row per unit: cursor, name, state, then "no timer" or the next run (Madrid)."""
    lines = services_text(_Services().services(), "hermes", _MADRID, BASE).plain.splitlines()
    assert [line.rstrip() for line in (lines[0], lines[3])] == [
        "▸ hermes        active (running)   no timer",
        "  daily         failed (failed)    next 10-03 18:00",
    ]


def test_every_panel_says_not_available_here_without_its_reader() -> None:
    """No systemd, no release directory, no machine reader: each panel says so."""
    texts = (
        services_text(None, None, _MADRID, BASE).plain,
        releases_text(None, None, _MADRID, BASE).plain,
        machine_text(None, _FROZEN, _MADRID, BASE).plain,
    )
    assert [text.split(" · ")[:2] for text in texts] == [
        ["Services", f"{NOT_HERE}; it reads the VPS (hermes --vps tui)"],
        ["Releases", f"{NOT_HERE}; it reads the VPS (hermes --vps tui)"],
        ["Machine", f"{NOT_HERE}; it reads the VPS (hermes --vps tui)"],
    ]


def test_a_figure_the_machine_cannot_read_says_not_available_here() -> None:
    """A PC without /proc: cpu and memory say not available here; the disk meter shows."""
    stats = MachineStats(disk_used=0.5, disk_free_bytes=2_000_000_000)
    lines = machine_text(stats, _FROZEN, _MADRID, BASE).plain.splitlines()
    assert lines == [
        f"cpu            {NOT_HERE}",
        f"memory         {NOT_HERE}",
        "disk           ▕█████░░░░░▏ 50% · 2.0 GB free",
        f"ledger         {NOT_HERE}",
        f"last snapshot  {NOT_HERE}",
    ]


def test_machine_rows_on_the_vps() -> None:
    """Meters with percentages, the ledger size and the last snapshot's age."""
    lines = machine_text(_STATS, _FROZEN, _MADRID, BASE).plain.splitlines()
    assert lines == [
        "cpu            ▕████░░░░░░▏ 42%",
        "memory         ▕██████░░░░▏ 61%",
        "disk           ▕█████████░▏ 90% · 4.2 GB free",
        "ledger         12.3 MB",
        f"last snapshot  pre-{_LIVE}.sqlite3 · 10-02 15:58 · 22 h ago",
    ]


def test_timing_without_data_says_not_available_yet() -> None:
    """No heartbeat, no ECB rate, no bar checks: each row says not available yet."""
    text = timing_text(
        None, eur_rate_date=None, bar_seconds=(), now=_FROZEN, tz=_MADRID, tokens=BASE
    )
    assert text.plain.splitlines() == [
        f"market data    {NOT_YET}",
        f"order book     {NOT_YET} (fetched only when a signal fills)",
        f"EUR rate       {NOT_YET}",
        f"bar-check time {NOT_YET}",
    ]


def test_timing_with_data_shows_freshness_and_the_sparkline() -> None:
    """Last bar check and its age, the ECB rate date, the sparkline with last and max."""
    heartbeat = Heartbeat(
        state=HeartbeatState.OK,
        last_check_at=datetime(2026, 10, 3, 12, tzinfo=UTC),
        next_check_at=datetime(2026, 10, 3, 16, tzinfo=UTC),
        as_of=_FROZEN,
    )
    text = timing_text(
        heartbeat,
        eur_rate_date=date(2026, 10, 2),
        bar_seconds=_BarTiming().bar_check_seconds(30),
        now=_FROZEN,
        tz=_MADRID,
        tokens=BASE,
    )
    lines = text.plain.splitlines()
    assert (lines[0], lines[2], lines[3]) == (
        "market data    last bar check 10-03 14:00 · 12 min ago",
        "EUR rate       ECB rate of 2026-10-02",
        "bar-check time ▂▃▁█▂▂▅ last 6 s · max 9 s",
    )


def test_releases_list_the_live_one_first_with_roles() -> None:
    """Live since the link flip, previous and an unfinished install."""
    lines = releases_text(_history(), _PREVIOUS, _MADRID, BASE).plain.splitlines()
    assert lines == [
        f"  {_LIVE}  live      live since 10-02 16:02",
        f"▸ {_PREVIOUS}  previous  installed 09-30 11:00",
        f"  {_EARLIER}  earlier   install unfinished",
    ]


def test_release_detail_names_the_rollback_target_and_the_deploy_steps() -> None:
    """The live release rolls back to previous; the ten install_release.sh steps follow."""
    detail = release_detail(_history(), _LIVE, _FROZEN, _MADRID, BASE)
    text = _plain(detail)
    assert (
        f"Rollback to    {_PREVIOUS}" in text,
        "  10. Refresh /opt/hermes/bin and prune old releases" in text,
        "Step timings: not recorded yet." in text,
    ) == (True, True, True)


def test_an_earlier_release_has_no_rollback_target() -> None:
    """Only the live release rolls back; an earlier one says so."""
    text = _plain(release_detail(_history(), _PREVIOUS, _FROZEN, _MADRID, BASE))
    assert "Rollback to    only the live release rolls back" in text


def test_a_release_that_left_the_disk_says_so() -> None:
    """After a prune the detail of a gone release is one plain line."""
    assert _plain(release_detail(_history(), "000000000000", _FROZEN, _MADRID, BASE)) == (
        "Release 000000000000 is no longer on disk."
    )


def test_service_detail_shows_the_unit_timer_and_journal_state() -> None:
    """Unit, state, timer, next and last run, then the journal heading."""
    text = _plain(
        service_detail(
            _Services().services(),
            "daily",
            journal=None,
            now=_FROZEN,
            tz=_MADRID,
            tokens=BASE,
        )
    )
    assert text.splitlines() == [
        "Unit           hermes-daily.service",
        "State          failed (failed)",
        "Since          10-01 10:00 · 2 d ago",
        "Timer          hermes-daily.timer · armed",
        "Next run       10-03 18:00",
        "Last run       10-03 14:00",
        "",
        "Recent journal lines",
        f"The journal is {NOT_HERE}.",
    ]


def test_actions_are_commands_to_copy_and_nothing_runs() -> None:
    """Bootstrap, restart, rollback and the diagnosis appear as "$ command" lines."""
    commands = [
        line.strip().removeprefix("$ ")
        for line in actions_text(BASE).plain.splitlines()
        if line.strip().startswith("$ ")
    ]
    assert (actions_text(BASE).plain.splitlines()[0], commands) == (
        "Copy a command and run it yourself; nothing runs from this screen.",
        [
            "gh workflow run vps-bootstrap.yml --repo ricardoafo/hermes --ref main",
            "ssh hermes-01 systemctl restart hermes.service",
            "ssh hermes-01 /opt/hermes/bin/rollback.sh",
            "hermes status",
        ],
    )


def _plain(renderable: object) -> str:
    out = io.StringIO()
    Console(file=out, width=120, color_system=None).print(renderable)
    return "\n".join(line.rstrip() for line in out.getvalue().splitlines())


# ----------------------------------------------------------------------
# The screen in the app
# ----------------------------------------------------------------------


def test_tab_seven_shows_every_panel_from_the_readers() -> None:
    """Services, Releases, timing, Machine and Actions are all drawn on tab 8."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, list[str]]:
        text = _screen_text(app)
        found = [
            title
            for title in ("SERVICES", "RELEASES", "DATA AND TIMING", "MACHINE", "ACTIONS")
            if title in text
        ]
        return app.tab, found

    assert _run(probe) == (
        _SYSTEM_TAB,
        ["SERVICES", "RELEASES", "DATA AND TIMING", "MACHINE", "ACTIONS"],
    )


def test_without_readers_the_screen_says_not_available_here() -> None:
    """HomeApp without system sources (a test or a PC) shows "not available here"."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        return [
            app.plain_text(widget).split(" · ")[0]
            for widget in ("sys-services", "sys-releases", "sys-machine")
            if NOT_HERE in app.plain_text(widget)
        ]

    assert _run(probe, use_default=True) == ["Services", "Releases", "Machine"]


_SPAWNERS = (
    (subprocess, "run"),
    (subprocess, "Popen"),
    (subprocess, "call"),
    (subprocess, "check_call"),
    (subprocess, "check_output"),
    (os, "system"),
    (os, "popen"),
    (os, "execv"),
    (os, "execvp"),
    (asyncio, "create_subprocess_exec"),
    (asyncio, "create_subprocess_shell"),
)


# SEV-1: Security axis S7 - a console key must never run a VPS action (restart, rollback).
def test_no_key_on_the_system_screen_starts_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """With every process starter patched to fail, walking the screen starts none.

    The walk opens every service and release detail and presses the keys an
    operator might expect to act (B R K, Enter, Space). The fake readers run no
    command, so any attempt would come from the screen itself.
    """
    attempts: list[str] = []

    def refuse(name: str) -> Callable[..., object]:
        def spawn(*args: object, **kwargs: object) -> object:
            attempts.append(name)
            raise AssertionError(f"{name} called from the System screen")

        return spawn

    for module, name in _SPAWNERS:
        monkeypatch.setattr(module, name, refuse(f"{module.__name__}.{name}"))

    keys = ["b", "B", "r", "R", "k", "K", "space"]
    for _ in range(10):
        keys += ["down", "enter", "right_square_bracket", "escape"]

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int]:
        await _press(pilot, keys)
        return app.is_running, app.tab

    assert (_run(probe, size=(80, 24)), _run(probe), attempts) == (
        (True, _SYSTEM_TAB),
        (True, _SYSTEM_TAB),
        [],
    )


def test_no_key_is_bound_to_a_vps_action() -> None:
    """B and K (the MVP2 action keys) are bound to nothing; no action names a VPS change.

    Lower-case k stays the vim-style "previous" cursor key. R restarts this
    console onto a new local version (only while its notice shows); it changes
    nothing on the VPS, so it is the one allowed ``restart`` action.
    """
    bindings = [b for b in HomeApp.BINDINGS if isinstance(b, Binding)]
    keys = {key.strip() for binding in bindings for key in binding.key.split(",")}
    actions = {binding.action.split("(")[0] for binding in bindings}
    assert (
        {"b", "B", "K"} & keys,
        {"bootstrap", "rollback", "run", "execute"} & actions,
    ) == (set(), set())


def test_enter_on_a_release_opens_the_detail_screen_with_the_breadcrumb() -> None:
    """Down past the seven services lands on the live release; Enter opens its screen."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str, bool]:
        await _press(pilot, ["down"] * 7 + ["enter"])
        text = _screen_text(app)
        return (
            isinstance(app.screen, DetailScreen),
            text.splitlines()[0].strip(),
            f"Rollback to    {_PREVIOUS}" in text,
        )

    assert _run(probe) == (True, f"◂ System › Releases › {_LIVE}", True)


def test_next_and_back_keep_the_cursor_on_the_release_shown_last() -> None:
    """] moves the detail to the previous release; Esc returns with the cursor on it."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, int, str]:
        await _press(pilot, ["down"] * 7 + ["enter", "right_square_bracket"])
        crumb = _screen_text(app).splitlines()[0].strip()
        await _press(pilot, ["escape"])
        return crumb, len(app.screen_stack), app.plain_text("sys-releases").splitlines()[1]

    assert _run(probe) == (
        f"◂ System › Releases › {_PREVIOUS}",
        1,
        f"▸ {_PREVIOUS}  previous  installed 09-30 11:00",
    )


def test_enter_on_a_service_opens_the_pane_when_wide_and_esc_closes_it() -> None:
    """At 160 columns the service detail opens beside the list; its journal is read."""
    journal = _Journal()

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int, bool]:
        await _press(pilot, ["down", "down", "enter"])
        pane = app.query_one("#sys-service-pane", DetailPane)
        opened, screens = pane.is_open, len(app.screen_stack)
        await _press(pilot, ["escape"])
        return opened, screens, pane.is_open

    assert (_run(probe, system=_sources(journal)), journal.units[-1]) == (
        (True, 1, False),
        "hermes-health.service",
    )


def test_enter_on_a_service_opens_a_detail_screen_when_compact() -> None:
    """At 80 columns the service detail is a DetailScreen under the Services crumb."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str]:
        await _press(pilot, ["enter"])
        return isinstance(app.screen, DetailScreen), _screen_text(app).splitlines()[0].strip()

    assert _run(probe, size=(80, 24)) == (True, "◂ System › Services › hermes")


def test_system_sources_off_the_vps_have_no_bar_timing_without_a_ledger(tmp_path: Path) -> None:
    """The wiring builds every reader; no ledger file means no bar timing (not a crash)."""
    sources = system_sources(
        db_path=tmp_path / "absent.sqlite3", live=lambda: LiveRelease("unknown", None)
    )
    assert (
        sources.bar_timing,
        type(sources.services).__name__,
        type(sources.releases).__name__,
        type(sources.machine).__name__,
    ) == (None, "SystemctlServiceReader", "ReleaseDirectoryReader", "ProcMachineReader")


# ----------------------------------------------------------------------
# Snapshots (motion off, frozen clock)
# ----------------------------------------------------------------------


_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)


def _svg(size: tuple[int, int], theme: ThemeChoice) -> str:
    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        return repo_clean(app.export_screenshot())

    return _run(probe, size=size, theme=theme)


@pytest.mark.parametrize(
    ("theme_name", "theme", "size"),
    [
        pytest.param(name, theme, size, id=f"system_{name}_{size[0]}x{size[1]}")
        for name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_system_svg_snapshot(
    snapshot: SnapshotAssertion, theme_name: str, theme: ThemeChoice, size: tuple[int, int]
) -> None:
    """The reviewed System screen in one theme at one of the four spec breakpoints."""
    name = f"system_{theme_name}_{size[0]}x{size[1]}"
    assert _svg(size, theme) == snapshot(extension_class=NamedSVG, name=name)
