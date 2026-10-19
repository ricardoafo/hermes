"""The console's cold start screen (#1020), its checks and its rows.

Invariants:

- each start-up check reports what it found, or why it failed; an exception
  in a check becomes a failed row and never stops the console;
- a ledger with no bar check yet reads as a first run and names when the
  first check is due; a late or down engine fails its row;
- no journal, demo data (no ticker) and a failing ticker each fail their
  own row with the reason;
- the checks run in a worker thread, never on the UI thread;
- the screen opens over Home only with boot and motion on, closes on its own
  after the last check, closes at once on any key, and Q quits;
- reviewed SVG snapshots of the finished checklist at 80x24, 120x36, 160x45
  and 240x60 in both day themes.
"""

import asyncio
import threading
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from syrupy.assertion import SnapshotAssertion
from textual.app import App

from hermes.adapters.console import FixtureReadModel
from hermes.application.console.logs import LogLine
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_boot import (
    BootCheck,
    BootResult,
    BootScreen,
    boot_checks,
    boot_line,
    run_check,
)
from hermes.entrypoints.tui_theme import (
    BASE,
    CONTROL_THEME,
    NEON_THEME,
    THEMES,
    data_tokens,
    terminal_theme,
    theme_variables,
    tokens_for,
)
from tests.support.tui_views import assert_view, capture

_MADRID = ZoneInfo("Europe/Madrid")
_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, str], ...] = (("neon", NEON_THEME), ("control", CONTROL_THEME))


class _Ticker:
    """A live ticker that answers once, or fails with ``error``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.asked: list[Pair] = []

    def get_ticker(self, pair: Pair) -> Ticker:
        self.asked.append(pair)
        if self.error is not None:
            raise self.error
        return Ticker(
            pair=pair,
            best_bid=Decimal(60000),
            best_ask=Decimal(60001),
            last_price=Decimal(60000),
            snapshot_time=datetime.fromisoformat("2026-10-03T12:00:00+00:00"),
        )


def _results(
    scenario: str = "ok-two-positions",
    *,
    journal: list[LogLine] | None = None,
    ticker: _Ticker | None = None,
) -> dict[str, BootResult]:
    def lines(count: int) -> Sequence[LogLine] | None:
        del count
        return journal

    checks = boot_checks(
        FixtureReadModel.load(scenario),
        tz=_MADRID,
        journal=lines if journal is not None else None,
        ticker=ticker,
    )
    return {result.label: result for result in map(run_check, checks)}


def test_a_failing_check_becomes_a_failed_row_with_its_message() -> None:
    """An exception inside a check is reported on its row, not raised."""

    def broken() -> str:
        msg = "disk I/O error"
        raise OSError(msg)

    assert run_check(BootCheck("ledger", broken)) == BootResult(
        "ledger", ok=False, detail="disk I/O error"
    )


def test_the_checks_show_in_the_designed_order() -> None:
    """Ledger, engine, journal, market feed, AI supervisor, as the design spec lists them."""
    assert list(_results()) == ["ledger", "engine", "journal", "market feed", "AI supervisor"]


def test_a_ledger_without_bar_checks_reads_as_a_first_run() -> None:
    """The empty scenario has never checked a bar: the row names the first due check."""
    assert _results("empty")["ledger"] == BootResult(
        "ledger", ok=True, detail="first run · first bar check due 18:00"
    )


def test_a_ledger_with_bar_checks_names_the_last_one() -> None:
    """With checks recorded, the row names the last one in Madrid time."""
    assert _results()["ledger"].detail == "last bar check 14:00"


@pytest.mark.parametrize(
    ("scenario", "detail"),
    [
        ("late", "late · next check was due 18:00"),
        ("down-stale", "down · next check was due 22:00"),
    ],
)
def test_a_late_or_down_engine_fails_its_row(scenario: str, detail: str) -> None:
    """The engine row fails when the bar loop is not on time, saying since when."""
    assert _results(scenario)["engine"] == BootResult("engine", ok=False, detail=detail)


def test_no_journal_fails_its_row() -> None:
    """Where the journal cannot be read the row says so."""
    assert _results()["journal"] == BootResult("journal", ok=False, detail="not readable here")


def test_a_readable_journal_passes() -> None:
    """An empty but readable journal is still readable."""
    assert _results(journal=[])["journal"] == BootResult("journal", ok=True, detail="readable")


def test_demo_data_fails_the_market_feed_row() -> None:
    """Without a ticker there are no live prices to check."""
    assert _results()["market feed"].detail == "demo data · no live prices"


def test_a_live_ticker_passes_after_one_btc_quote() -> None:
    """The feed row asks Kraken for BTC once and passes on an answer."""
    ticker = _Ticker()
    result = _results(ticker=ticker)["market feed"]
    assert (result.ok, result.detail, [p.base.value for p in ticker.asked]) == (
        True,
        "Kraken prices every 10 s",
        ["BTC"],
    )


def test_a_failing_ticker_fails_the_market_feed_row_with_its_error() -> None:
    """A network failure shows on the row instead of stopping the start."""
    result = _results(ticker=_Ticker(ConnectionError("Kraken unreachable")))["market feed"]
    assert (result.ok, result.detail) == (False, "Kraken unreachable")


def test_rows_read_pending_passed_and_failed() -> None:
    """A row shows … while checking, ✓ with what was found, ✗ with why."""
    tokens = tokens_for(NEON_THEME)
    rows = [
        boot_line("ledger", None, tokens).plain,
        boot_line(
            "ledger", BootResult("ledger", ok=True, detail="last bar check 14:00"), tokens
        ).plain,
        boot_line(
            "journal", BootResult("journal", ok=False, detail="not readable here"), tokens
        ).plain,
    ]
    assert rows == [
        "…  ledger         checking",
        "✓  ledger         last bar check 14:00",
        "✗  journal        not readable here",
    ]


# ----------------------------------------------------------------------
# The screen inside the console
# ----------------------------------------------------------------------


def _home(*, boot: bool = True, motion: bool = True) -> HomeApp:
    return HomeApp(
        FixtureReadModel.load("ok-two-positions"),
        refresh_seconds=3600,
        motion=motion,
        boot=boot,
        release=lambda: "1.0.0-shadow.7",
    )


def test_the_console_opens_on_the_boot_screen_with_boot_and_motion_on() -> None:
    """``hermes tui`` passes boot: the first screen over Home is the cold start screen."""

    async def drive() -> bool:
        app = _home()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            return isinstance(app.screen, BootScreen)

    assert asyncio.run(drive()) is True


@pytest.mark.parametrize(("boot", "motion"), [(True, False), (False, True)])
def test_no_boot_screen_with_motion_off_or_without_boot(*, boot: bool, motion: bool) -> None:
    """Motion off skips the start-up sequence, as the design spec says; so do tests and embeds."""

    async def drive() -> int:
        app = _home(boot=boot, motion=motion)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            return len(app.screen_stack)

    assert asyncio.run(drive()) == 1


def test_the_boot_screen_fills_every_row_then_closes_on_its_own() -> None:
    """After the last check and a short hold, Home shows without a key."""

    async def drive() -> tuple[int, int]:
        app = _home()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            boot = app.screen
            assert isinstance(boot, BootScreen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            filled = sum(result is not None for result in boot.results)
            await pilot.pause(1.2)
            return filled, len(app.screen_stack)

    assert asyncio.run(drive()) == (5, 1)


def test_any_key_opens_the_console_at_once() -> None:
    """Pressing a key closes the boot screen before the checks finish their hold."""

    async def drive() -> int:
        app = _home()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            return len(app.screen_stack)

    assert asyncio.run(drive()) == 1


def test_q_on_the_boot_screen_quits() -> None:
    """Q quits from the boot screen as it does from Home."""

    async def drive() -> bool:
        app = _home()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            return app.is_running

    assert asyncio.run(drive()) is False


def test_checks_run_off_the_ui_thread() -> None:
    """A slow check never blocks keys or animations: it runs in a worker thread."""
    seen: list[bool] = []

    def where() -> str:
        seen.append(threading.current_thread() is threading.main_thread())
        return "ok"

    async def drive() -> None:
        app = _BootApp(NEON_THEME, (BootCheck("ledger", where),))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()

    asyncio.run(drive())
    assert seen == [False]


def test_quitting_while_a_check_runs_ends_the_checks_quietly() -> None:
    """A check still in flight when Q quits finishes without an error and stops the rest."""
    ran: list[str] = []

    def ledger() -> str:
        ran.append("ledger")
        return "ok"

    def engine() -> str:
        ran.append("engine")
        return "ok"

    async def drive() -> BootScreen:
        app = _BootApp(NEON_THEME, ())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("q")
            await pilot.pause()
        assert isinstance(screen, BootScreen)
        return screen

    screen = asyncio.run(drive())
    screen._checks = (BootCheck("ledger", ledger), BootCheck("engine", engine))
    errors: list[BaseException] = []

    def after_quit() -> None:
        try:
            screen._run_checks()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=after_quit)
    worker.start()
    worker.join(timeout=5)
    assert (errors, ran) == ([], ["ledger"])


# ----------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------


_SNAPSHOT_CHECKS = (
    BootCheck("ledger", lambda: "last bar check 14:00"),
    BootCheck("engine", lambda: "on time · next check 18:00"),
    BootCheck("journal", lambda: "readable"),
    BootCheck("market feed", lambda: "Kraken prices every 10 s"),
    BootCheck("AI supervisor", lambda: "1 brief published"),
)


class _BootApp(App[None]):
    """Hosts the boot screen alone, with a hold long enough to snapshot it."""

    def __init__(self, theme: str, checks: Sequence[BootCheck]) -> None:
        super().__init__()
        for hermes_theme in THEMES:
            self.register_theme(hermes_theme)
        self.ansi_theme_dark = terminal_theme(data_tokens(theme))
        self.theme = theme
        self._checks = checks

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return theme_variables(BASE)

    def on_mount(self) -> None:
        self.push_screen(
            BootScreen(
                self._checks,
                lambda: tokens_for(self.theme),
                release="1.0.0-shadow.7",
                hold=3600,
            )
        )


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _svg(theme: str, size: tuple[int, int]) -> str:
    async def drive() -> str:
        app = _BootApp(theme, _SNAPSHOT_CHECKS)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return capture(app)

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("theme_name", "theme", "size"),
    [(name, theme, size) for name, theme in _THEMES for size in _SIZES],
    ids=[f"{name}-{w}x{h}" for name, _ in _THEMES for w, h in _SIZES],
)
def test_finished_boot_screen_snapshot(
    snapshot: SnapshotAssertion, theme_name: str, theme: str, size: tuple[int, int]
) -> None:
    """The finished checklist only changes when a reviewer accepts the new picture."""
    width, height = size
    assert_view(snapshot, _svg(theme, size), name=f"boot_{theme_name}_{width}x{height}")
