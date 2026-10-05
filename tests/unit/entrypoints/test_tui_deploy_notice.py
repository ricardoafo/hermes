"""The console's new-version notice and R restart (issue #652).

An open console keeps running the code it started with. Each test protects one
rule: the notice shows only when the running release changed since start, R
re-execs with the original command line, and a failing reader never breaks the
screen. The release reader and the exec function are fakes; nothing execs.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from hermes.adapters.console import FixtureReadModel
from hermes.entrypoints import tui as tui_module
from hermes.entrypoints.tui import HomeApp, run_home

_NOTICE = "new version deployed · press R to restart"


class _Release:
    """A release reader whose value, or failure, the test flips."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fail = False

    def __call__(self) -> str:
        if self.fail:
            raise OSError("link unreadable")
        return self.name


def _app(reader: _Release) -> HomeApp:
    return HomeApp(FixtureReadModel.load("ok-two-positions"), release=reader)


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
