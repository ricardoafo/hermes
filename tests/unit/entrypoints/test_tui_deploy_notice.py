"""The console's new-version notice and R restart (issue #652).

An open console keeps running the code it started with. Each test protects one
rule: the notice shows only when the running release changed since start, R
re-execs with the original command line, and a failing reader never breaks the
screen. The release reader and the exec function are fakes; nothing execs.
"""

import asyncio
import sys
from datetime import UTC, datetime

import pytest

from hermes.adapters.console import FixtureReadModel
from hermes.entrypoints import tui as tui_module
from hermes.entrypoints.tui import HomeApp, run_home
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import NEON

_NOTICE = "new version deployed · press R to restart"
# 14:12 Madrid: inside Auto's day window, so no night theme flips the colours.
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)


class _Release:
    """A release reader whose value, or failure, the test flips."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fail = False

    def __call__(self) -> str:
        if self.fail:
            raise OSError("link unreadable")
        return self.name


def _app(reader: _Release, theme: ThemeChoice | None = None) -> HomeApp:
    return HomeApp(
        FixtureReadModel.load("ok-two-positions"),
        release=reader,
        clock=lambda: _FROZEN,
        motion=False,
        theme=theme,
    )


def test_no_notice_when_the_release_is_unchanged() -> None:
    """Invariant: the same release on every tick shows no notice and R does not quit."""
    reader = _Release("aaaaaaaaaaaa")

    async def run() -> tuple[str, bool, bool]:
        app = _app(reader)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._redraw()
            await pilot.press("r")
            await pilot.pause()
            return app.plain_text("notice").strip(), app.restart_requested, app.is_running

    assert asyncio.run(run()) == ("", False, True)


def test_notice_shows_after_the_release_changes() -> None:
    """Invariant: a flip of the release link after start shows the notice on the next tick."""
    reader = _Release("aaaaaaaaaaaa")

    async def run() -> tuple[str, str]:
        app = _app(reader)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            before = app.plain_text("notice").strip()
            reader.name = "bbbbbbbbbbbb"
            app._redraw()
            await pilot.pause()
            return before, app.plain_text("notice").strip()

    assert asyncio.run(run()) == ("", _NOTICE)


def test_r_after_a_change_asks_for_a_restart_and_quits() -> None:
    """Invariant: R with the notice up flags the restart and exits the app cleanly."""
    reader = _Release("aaaaaaaaaaaa")

    async def run() -> bool:
        app = _app(reader)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            reader.name = "bbbbbbbbbbbb"
            app._redraw()
            await pilot.press("r")
            await pilot.pause()
        return app.restart_requested

    assert asyncio.run(run()) is True


@pytest.mark.parametrize("phase", ["at_start", "after_start"])
def test_reader_error_neither_crashes_nor_shows_the_notice(phase: str) -> None:
    """Invariant: a release reader that raises, at start or later, leaves no notice and no crash."""
    reader = _Release("aaaaaaaaaaaa")
    reader.fail = phase == "at_start"

    async def run() -> tuple[str, bool]:
        app = _app(reader)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            reader.fail = True
            app._redraw()
            await pilot.press("r")
            await pilot.pause()
            return app.plain_text("notice").strip(), app.restart_requested

    assert asyncio.run(run()) == ("", False)


def test_unknown_release_is_not_a_change() -> None:
    """Invariant: the reader's "unknown" (link missing mid-deploy) never raises the notice."""
    reader = _Release("aaaaaaaaaaaa")

    async def run() -> str:
        app = _app(reader)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            reader.name = "unknown"
            app._redraw()
            await pilot.pause()
            return app.plain_text("notice").strip()

    assert asyncio.run(run()) == ""


def test_notice_and_restart_work_on_another_tab() -> None:
    """Invariant: the notice sits above the screens, so tab 2 shows it and R restarts from there."""
    reader = _Release("aaaaaaaaaaaa")

    async def run() -> tuple[bool, bool]:
        app = _app(reader)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            reader.name = "bbbbbbbbbbbb"
            app._redraw()
            await pilot.pause()
            shown = app.query_one("#notice").display
            await pilot.press("r")
            await pilot.pause()
        return shown, app.restart_requested

    assert asyncio.run(run()) == (True, True)


def test_r_under_the_help_screen_does_not_restart() -> None:
    """Invariant: with help open over the console, R neither quits nor flags a restart."""
    reader = _Release("aaaaaaaaaaaa")

    async def run() -> tuple[bool, bool]:
        app = _app(reader)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            reader.name = "bbbbbbbbbbbb"
            app._redraw()
            await pilot.press("question_mark")
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            return app.restart_requested, app.is_running

    assert asyncio.run(run()) == (False, True)


def test_notice_row_sits_below_the_neon_window_bar_and_the_status_row() -> None:
    """Invariant: at 80x24 in Neon the notice has its own row, under the status row.

    Rows: window bar 0, tabs 1, status row 2, notice 3, screens from 4.
    """
    reader = _Release("aaaaaaaaaaaa")

    async def run() -> tuple[int, ...]:
        app = _app(reader, ThemeChoice.NEON)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            reader.name = "bbbbbbbbbbbb"
            app._redraw()
            await pilot.pause()
            rows = (
                app.query_one(f"#{widget}").region.y
                for widget in ("window-bar", "top", "notice", "screens")
            )
            return (*rows, app.query_one("#notice").region.height)

    assert asyncio.run(run()) == (0, 2, 3, 4, 1)


def test_notice_is_drawn_in_the_theme_warn_token() -> None:
    """Invariant: the notice's text is the theme's amber ``warn`` token, not a raw colour."""
    reader = _Release("aaaaaaaaaaaa")

    async def run() -> str:
        app = _app(reader, ThemeChoice.NEON)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            reader.name = "bbbbbbbbbbbb"
            app._redraw()
            await pilot.pause()
            return app.query_one("#notice").styles.color.hex.upper()

    assert asyncio.run(run()) == NEON.warn.upper()


def _fake_run(monkeypatch: pytest.MonkeyPatch, *, restart: bool) -> None:
    def run(self: HomeApp, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.restart_requested = restart

    monkeypatch.setattr(tui_module.HomeApp, "run", run)


def test_restart_execs_the_interpreter_with_the_original_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: after a clean exit with a restart request, exec gets the same argv."""
    _fake_run(monkeypatch, restart=True)
    calls: list[tuple[str, list[str]]] = []
    run_home(
        FixtureReadModel.load("ok-two-positions"),
        exec_fn=lambda path, args: calls.append((path, args)),
        argv=["hermes", "tui", "--db", "x.db"],
        platform="linux",
    )
    assert calls == [(sys.executable, [sys.executable, "hermes", "tui", "--db", "x.db"])]


def test_no_exec_without_a_restart_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: a plain quit never execs."""
    _fake_run(monkeypatch, restart=False)
    calls: list[str] = []
    run_home(
        FixtureReadModel.load("ok-two-positions"),
        exec_fn=lambda path, args: calls.append(path),
        platform="linux",
    )
    assert calls == []


def test_windows_exits_with_a_message_instead_of_exec(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invariant: where exec is not meaningful the console prints a hint and does not exec."""
    _fake_run(monkeypatch, restart=True)
    calls: list[str] = []
    run_home(
        FixtureReadModel.load("ok-two-positions"),
        exec_fn=lambda path, args: calls.append(path),
        platform="win32",
    )
    assert (calls, capsys.readouterr().out) == (
        [],
        "New version deployed: start hermes again to use it.\n",
    )
