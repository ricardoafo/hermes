"""The console's reads: each source once per poll, and never on the UI thread (issue #1001).

Invariants:

- :func:`read_console` calls each read-model method at most once per read, and
  a screen's own read (Briefs, Signals, Logs, Tax) only while that screen shows.
- A failed read keeps its error in its own field; every other card still reads.
- With background reads, a screen switch paints the new tab before a slow read
  returns, and quitting while a read is in flight ends quietly.
- ``run_home``, the console's entry point, reads in the background.
"""

import asyncio
import threading
from collections import Counter
from datetime import UTC, datetime
from typing import Final

import pytest
from textual.widgets import ContentSwitcher

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import PortfolioRange, ViewNotAvailableError
from hermes.application.console.shadow_screen import ShadowSources
from hermes.application.console.system import SystemSources
from hermes.application.console.views import TaxPending
from hermes.domain.errors import LedgerError
from hermes.entrypoints.tui import HomeApp, run_home
from hermes.entrypoints.tui_reads import ReadRequest, ReadSources, SystemReads, read_console

_FROZEN: Final = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_METHODS: Final = (
    "heartbeat",
    "portfolio",
    "positions",
    "position",
    "activity",
    "rule_checks",
    "market_watch",
    "briefs",
)


class _CountingModel:
    """Delegates to a fixture and counts every read-model method called."""

    def __init__(self, scenario: str = "ok-two-positions", *, live: bool = False) -> None:
        self._inner = FixtureReadModel.load(scenario)
        self._live = live
        self.calls: Counter[str] = Counter()

    @property
    def demo_scenario(self) -> str | None:
        return None if self._live else self._inner.demo_scenario

    def __getattr__(self, name: str) -> object:
        attr = getattr(self._inner, name)
        if name not in _METHODS:
            return attr

        def counted(*args: object, **kwargs: object) -> object:
            self.calls[name] += 1
            return attr(*args, **kwargs)

        return counted


class _BrokenMarket(_CountingModel):
    """A model whose market watch cannot be read."""

    def market_watch(self) -> None:
        raise LedgerError("disk I/O error")


def _sources(model: object, **kwargs: object) -> ReadSources:
    return ReadSources(model, ShadowSources(), **kwargs)  # type: ignore[arg-type]


def test_one_read_calls_market_watch_and_portfolio_once() -> None:
    """A poll reads the market watch and the portfolio once each (they were read twice)."""
    model = _CountingModel()
    read_console(_sources(model), ReadRequest(width=80))
    assert (model.calls["market_watch"], model.calls["portfolio"]) == (1, 1)


def test_one_read_reads_each_open_position_once() -> None:
    """Each of the two demo positions has its detail read exactly once per poll."""
    model = _CountingModel()
    read_console(_sources(model), ReadRequest(width=80))
    assert model.calls["position"] == 2


def test_home_reads_skip_the_screens_that_are_not_shown() -> None:
    """Briefs and rule checks are not read while their screens are hidden."""
    model = _CountingModel()
    reads = read_console(_sources(model), ReadRequest(width=80))
    assert (model.calls["briefs"], model.calls["rule_checks"], reads.briefs, reads.tax) == (
        0,
        0,
        None,
        None,
    )


def test_the_shown_screen_reads_its_own_source() -> None:
    """With Briefs and Signals shown, each of their sources is read once."""
    model = _CountingModel()
    read_console(_sources(model), ReadRequest(width=80, briefs=True, signals=True))
    assert (model.calls["briefs"], model.calls["rule_checks"]) == (1, 1)


def test_the_requested_range_reaches_the_portfolio_read() -> None:
    """The week range is the range the portfolio is read over."""
    reads = read_console(
        _sources(_CountingModel()), ReadRequest(width=80, range_=PortfolioRange.WEEK)
    )
    assert getattr(reads.portfolio, "range", None) is PortfolioRange.WEEK


def test_a_failed_read_keeps_its_error_and_the_rest_still_read() -> None:
    """A broken market watch is that card's error; the positions still read."""
    reads = read_console(_sources(_BrokenMarket()), ReadRequest(width=80))
    coins = [row.coin for row in getattr(reads.positions, "__iter__", list)()]
    assert (type(reads.watch), str(reads.watch), coins) == (
        LedgerError,
        "Ledger error: disk I/O error",
        ["ETH", "SOL"],
    )


def test_logs_lines_come_from_the_journal_only_when_asked() -> None:
    """The journal is asked for the Logs screen's 50 lines, and only when that screen shows."""
    asked: list[int] = []

    def journal(lines: int) -> list[object]:
        asked.append(lines)
        return []

    model = _CountingModel(live=True)
    read_console(_sources(model, journal=journal), ReadRequest(width=80))
    reads = read_console(_sources(model, journal=journal), ReadRequest(width=80, logs_lines=50))
    assert (asked, reads.logs, reads.logs_asked) == ([50], [], True)


def test_a_demo_never_reads_the_journal() -> None:
    """A demo scenario has no journal of its own, so it is never read."""
    asked: list[int] = []

    def journal(lines: int) -> list[object]:
        asked.append(lines)
        return []

    reads = read_console(
        _sources(_CountingModel(), journal=journal),
        ReadRequest(width=80, logs_lines=50, log_tail_rows=10),
    )
    assert (asked, reads.logs, reads.log_tail) == ([], None, None)


def test_tax_without_a_source_says_the_estimate_is_not_available() -> None:
    """The Tax screen with no tax source reads as the pending line, without a pack."""
    reads = read_console(_sources(_CountingModel()), ReadRequest(width=80, tax=True))
    assert (reads.tax, reads.tax_pack) == (
        TaxPending(label="tax estimate not available yet"),
        None,
    )


def test_a_release_source_that_fails_reads_as_no_release() -> None:
    """A release read that raises leaves the release unknown, not the console down."""

    def release() -> str:
        raise OSError

    request = ReadRequest(width=80)
    failed = read_console(_sources(_CountingModel(), release=release), request)
    read = read_console(_sources(_CountingModel(), release=lambda: "1.0.0-shadow.1"), request)
    assert (failed.release, read.release) == (None, "1.0.0-shadow.1")


class _SlowModel(_CountingModel):
    """Holds every heartbeat read until the test lets it go."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.held = threading.Event()
        self.returned = 0

    def heartbeat(self) -> object:
        self.held.set()
        self.gate.wait(timeout=5)
        self.returned += 1
        return self._inner.heartbeat()


def test_a_screen_switch_paints_before_a_slow_read_returns() -> None:
    """Pressing 4 shows the Tax tab while the model read is still held."""
    model = _SlowModel()

    async def run() -> tuple[str | None, int, bool, int]:
        app = HomeApp(
            model,  # type: ignore[arg-type]
            clock=lambda: _FROZEN,
            motion=False,
            refresh_seconds=3600,
            background_reads=True,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            model.gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            model.gate.clear()
            model.held.clear()
            before = model.returned
            await pilot.press("4")
            current = app.query_one("#screens", ContentSwitcher).current
            tab = app.tab
            returned_meanwhile = model.returned - before
            held = model.held.wait(timeout=5)
            model.gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
        return current, tab, held, returned_meanwhile

    # The switch is on screen, the fresh read has started, and none has returned yet.
    assert asyncio.run(run()) == ("tab-4", 3, True, 0)


def test_quitting_while_a_read_is_held_ends_quietly() -> None:
    """Q during a held read exits cleanly, and a read finishing after the exit raises nothing."""
    model = _SlowModel()

    def late_read(app: HomeApp) -> str:
        try:
            app._read_off_thread(ReadRequest(width=80))
        except RuntimeError as exc:
            return repr(exc)
        return "dropped quietly"

    async def run() -> tuple[int | None, BaseException | None, str]:
        app = HomeApp(
            model,  # type: ignore[arg-type]
            clock=lambda: _FROZEN,
            motion=False,
            refresh_seconds=3600,
            background_reads=True,
        )
        async with app.run_test(size=(80, 24)) as pilot:
            model.held.wait(timeout=5)
            await pilot.press("q")
        model.gate.set()
        late = await asyncio.to_thread(late_read, app)
        return app.return_code, app._exception, late

    assert asyncio.run(run()) == (0, None, "dropped quietly")


class _RangeSpy(_SlowModel):
    """A held model that also records the range of every portfolio read."""

    def __init__(self) -> None:
        super().__init__()
        self.ranges: list[PortfolioRange] = []

    def portfolio(self, range_: PortfolioRange = PortfolioRange.TODAY) -> object:
        self.ranges.append(range_)
        return self._inner.portfolio(range_)


def test_redraws_during_a_held_read_run_one_more_read_with_the_newest_state() -> None:
    """Two redraws during a held read make exactly one more read, over the newest range."""
    model = _RangeSpy()

    async def run() -> tuple[list[PortfolioRange], PortfolioRange | None]:
        app = HomeApp(
            model,  # type: ignore[arg-type]
            clock=lambda: _FROZEN,
            motion=False,
            refresh_seconds=3600,
            background_reads=True,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            model.held.wait(timeout=5)
            await pilot.press("w")
            await pilot.press("m")
            model.gate.set()
            for _ in range(20):
                await app.workers.wait_for_complete()
                await pilot.pause()
            summary = app._summary
        return model.ranges, None if summary is None else summary.range

    # The held read was for today; W then M while it ran ask for one read, made over month.
    assert asyncio.run(run()) == (
        [PortfolioRange.TODAY, PortfolioRange.MONTH],
        PortfolioRange.MONTH,
    )


def test_run_home_reads_in_the_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """The console's entry point builds its app with background reads on."""
    started: list[HomeApp] = []

    def run(app: HomeApp) -> None:
        started.append(app)

    monkeypatch.setattr(HomeApp, "run", run)
    run_home(FixtureReadModel.load("ok-two-positions"), settings_path=None)
    assert [app._background_reads for app in started] == [True]


def test_the_view_error_is_kept_as_the_card_error() -> None:
    """A view not served yet is kept as that card's error, not raised."""

    class _NoActivity(_CountingModel):
        def activity(self, *, include_quiet: bool = False) -> None:
            del include_quiet
            raise ViewNotAvailableError("activity")

    reads = read_console(_sources(_NoActivity()), ReadRequest(width=80))
    assert (type(reads.activity), str(reads.activity)) == (ViewNotAvailableError, "activity")


class _CountingServices:
    """A services reader that counts its reads and finds no unit."""

    def __init__(self) -> None:
        self.reads = 0

    def services(self) -> tuple[()]:
        self.reads += 1
        return ()


def test_system_sources_are_read_only_while_the_system_screen_shows() -> None:
    """The service states are read once with the System screen shown, and not at all without it."""
    services = _CountingServices()
    sources = ReadSources(
        _CountingModel(),  # type: ignore[arg-type]
        ShadowSources(),
        system=SystemSources(services=services),
    )
    hidden = read_console(sources, ReadRequest(width=80))
    shown = read_console(sources, ReadRequest(width=80, system=True))
    assert (hidden.system, shown.system, services.reads) == (None, SystemReads(services=()), 1)
