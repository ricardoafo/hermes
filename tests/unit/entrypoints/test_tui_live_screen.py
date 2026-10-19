"""Live prices on the Cockpit home screen (issue #713), driven with Textual's pilot.

Every test uses a fake ticker; nothing reaches the network.

Invariants:

- the console asks the ticker for BTC, ETH and SOL when it opens, then once
  every 10 s of its clock, in a thread worker (A-1.1);
- a failing ticker never raises into the UI: the coin keeps its last price,
  dimmed, with "as of HH:MM:SS" (A-1.2);
- more than 60 s without a good tick pauses the live layer: the tags read
  "live · paused HH:MM:SS" and the big total dims (A-1.3);
- the big total, the position rows and the position detail follow the live
  prices, tagged live, beside the booked values (A-3.1 to A-3.3);
- a tick flashes the moved cells with motion on, and the flash ends; with
  motion off nothing flashes (A-3.4);
- ticks never write the ledger: its bytes are the same before and after (A-4.1);
- reviewed SVG snapshots with a fake ticker at 80x24, 120x36, 160x45 and
  240x60, motion off and a frozen clock (A-6.1).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from rich.console import Group
from rich.text import Text
from syrupy.assertion import SnapshotAssertion
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.application.console import ConsoleReadModel
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import tui
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_theme import BASE, MOTION
from hermes.entrypoints.tui_widgets import BigNumber
from tests.support.clock import FakeClock
from tests.support.tui_views import assert_view, capture
from tests.unit.adapters.console._ledger_builder import one_btc_position

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_CAP = Decimal("0.15")
_RECORD_SIZE = (160, 45)
_SIZES = ((80, 24), (120, 36), (160, 45), (240, 60))
_FIRST = {"BTC": "62000", "ETH": "2700", "SOL": "150"}
_SECOND = {"BTC": "62100", "ETH": "2710", "SOL": "149"}


class _FakeTicker:
    """Answers from a price table; raises for coins in ``fail``; records each pair asked."""

    def __init__(self, prices: dict[str, str]) -> None:
        self.prices = {coin: Decimal(price) for coin, price in prices.items()}
        self.fail: set[str] = set()
        self.calls: list[str] = []
        self.threads: set[str] = set()

    def set(self, prices: dict[str, str]) -> None:
        self.prices = {coin: Decimal(price) for coin, price in prices.items()}

    def get_ticker(self, pair: Pair) -> Ticker:
        coin = pair.base.value
        self.calls.append(f"{coin}/{pair.quote.value}")
        self.threads.add(threading.current_thread().name)
        if coin in self.fail:
            raise KrakenPublicError(f"no {coin}")
        price = self.prices[coin]
        return Ticker(
            pair=pair,
            best_bid=price - 1,
            best_ask=price + 1,
            last_price=price,
            snapshot_time=_FROZEN,
        )


async def _settle(app: HomeApp, pilot: Pilot[None]) -> None:
    """Wait for the ticker worker and the UI-thread hand-over."""
    await app.workers.wait_for_complete()
    await pilot.pause()


async def _advance(app: HomeApp, pilot: Pilot[None], clock: FakeClock, seconds: int) -> None:
    """Move the clock on and run one 1 s clock tick, as the app's timer does."""
    clock.current += timedelta(seconds=seconds)
    app._tick()
    await _settle(app, pilot)


def _run[T](
    model: ConsoleReadModel,
    fake: _FakeTicker,
    probe: Callable[[HomeApp, Pilot[None], FakeClock], Awaitable[T]],
    *,
    size: tuple[int, int] = (160, 45),
    motion: bool = False,
) -> T:
    clock = FakeClock(_FROZEN)

    async def drive() -> T:
        app = HomeApp(
            model,
            clock=clock,
            motion=motion,
            refresh_seconds=3600,
            exposure_cap=_CAP,
            ticker=fake,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await _settle(app, pilot)
            return await probe(app, pilot, clock)

    return asyncio.run(drive())


def _fixture() -> ConsoleReadModel:
    return FixtureReadModel.load("ok-two-positions")


def _line(app: HomeApp, widget: str, starts: str) -> str:
    return next(line for line in app.plain_text(widget).splitlines() if line.startswith(starts))


def _spans(content: object) -> list[tuple[str, str]]:
    texts = content.renderables if isinstance(content, Group) else [content]
    out = []
    for text in texts:
        if isinstance(text, Text):
            out += [(text.plain[s.start : s.end], str(s.style)) for s in text.spans]
    return out


# ----------------------------------------------------------------------
# Cadence and failure
# ----------------------------------------------------------------------


def test_ticker_is_read_on_open_then_every_ten_seconds_in_a_worker_thread() -> None:
    """Open, +9 s, +1 s, +5 s, +5 s: polls at 0, 10 and 20 s only; never on the UI thread."""
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> tuple[int, ...]:
        counts = [len(fake.calls)]
        for step in (9, 1, 5, 5):
            await _advance(app, pilot, clock, step)
            counts.append(len(fake.calls))
        return tuple(counts)

    assert _run(_fixture(), fake, probe) == (3, 3, 6, 6, 9)
    assert fake.calls[:3] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert threading.main_thread().name not in fake.threads


def test_failed_tick_keeps_the_last_price_dimmed_with_as_of() -> None:
    """ETH fails on the second poll: its row keeps €2,313, dim, and says as of 14:12:00."""
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> tuple[str, bool]:
        fake.fail = {"ETH"}
        await _advance(app, pilot, clock, 10)
        eth = _line(app, "market", "ETH")
        dim = (f"{'€2,313':>10}", BASE.dim) in _spans(app._content["market"])
        return eth, dim

    eth, dim = _run(_fixture(), fake, probe)
    assert (eth.split()[:5], dim) == (["ETH", "€2,313", "as", "of", "14:12:00"], True)


def test_a_ticker_that_never_answers_never_raises_into_the_ui() -> None:
    """Every coin fails from the start: the app runs, waits for prices and shows no price."""
    fake = _FakeTicker(_FIRST)
    fake.fail = {"BTC", "ETH", "SOL"}

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> tuple[bool, str, str]:
        await _advance(app, pilot, clock, 10)
        return app.is_running, app.plain_text("market").splitlines()[0], _line(app, "market", "BTC")

    running, tag, btc = _run(_fixture(), fake, probe)
    assert (running, tag, btc.split()[:2]) == (True, "○ live · waiting for prices", ["BTC", "…"])


def test_stale_ticker_pauses_the_live_values() -> None:
    """No good tick for 61 s: tags read paused at the last tick, the big total dims."""
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> tuple[str, str, bool]:
        fake.fail = {"BTC", "ETH", "SOL"}
        for _ in range(6):
            await _advance(app, pilot, clock, 10)
        await _advance(app, pilot, clock, 1)
        big = app.screen_stack[0].query_one("#portfolio-total", BigNumber)
        return app.plain_text("market").splitlines()[0], app.plain_text("positions"), big._stale

    tag, positions, stale = _run(_fixture(), fake, probe)
    assert (tag, positions.splitlines()[0], stale) == (
        "● live · paused 14:12:00",
        "● live · paused 14:12:00",
        True,
    )


# ----------------------------------------------------------------------
# Live values beside booked values
# ----------------------------------------------------------------------


def test_big_total_and_rows_follow_the_live_prices_beside_the_booked_values() -> None:
    """The big total moves off the booked €1,021.06; the booked total stays named below it."""
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> tuple[str, list[str]]:
        del pilot, clock
        big = app.screen_stack[0].query_one("#portfolio-total", BigNumber)
        return big.figure, app.plain_text("portfolio").splitlines()[:2]

    figure, lines = _run(_fixture(), fake, probe)
    assert figure != "€1,021.06"
    assert lines[0].startswith("● live · today ▲ ")
    assert lines[1] == "booked as of 14:00 · €1,021.06"


def test_position_detail_leads_with_the_live_line_above_the_booked_figures() -> None:
    """Enter opens ETH's detail: the live line, then where the booked figures are."""
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> list[str]:
        del clock
        await pilot.press("enter")
        await pilot.pause()
        return app.plain_text("detail").splitlines()[:2]

    lines = _run(_fixture(), fake, probe)
    assert lines[0].startswith("● live · now €2,313 · ▲ ")
    assert lines[1] == "booked as of 14:00 below"


# ----------------------------------------------------------------------
# Flash
# ----------------------------------------------------------------------


def test_tick_flashes_the_moved_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    """Motion on: ETH up flashes on the up token and SOL down on the down token.

    The flash outlasts the test, so a busy runner cannot end it before the
    read (issue #952).
    """
    monkeypatch.setattr(tui, "MOTION", replace(MOTION, flash=3600.0))
    fake = _FakeTicker(_FIRST)

    async def probe(
        app: HomeApp, pilot: Pilot[None], clock: FakeClock
    ) -> tuple[dict[str, int], bool]:
        fake.set(_SECOND)
        await _advance(app, pilot, clock, 10)
        up = f"bold {BASE.void} on {BASE.up}"
        return app.flashing, any(style == up for _, style in _spans(app._content["market"]))

    assert _run(_fixture(), fake, probe, motion=True) == ({"BTC": 1, "ETH": 1, "SOL": -1}, True)


def test_the_tick_flash_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Motion on: the flash a tick starts is gone once its duration has passed.

    A short flash keeps the wait well past it however slow the runner is
    (issue #952).
    """
    monkeypatch.setattr(tui, "MOTION", replace(MOTION, flash=0.05))
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> dict[str, int]:
        fake.set(_SECOND)
        await _advance(app, pilot, clock, 10)
        await pilot.pause(1.0)
        return app.flashing

    assert _run(_fixture(), fake, probe, motion=True) == {}


def test_motion_off_never_flashes() -> None:
    """Motion off: the same moves flash nothing, and no cell has a flash background."""
    fake = _FakeTicker(_FIRST)

    async def probe(
        app: HomeApp, pilot: Pilot[None], clock: FakeClock
    ) -> tuple[dict[str, int], bool]:
        fake.set(_SECOND)
        await _advance(app, pilot, clock, 10)
        spans = _spans(app._content["market"]) + _spans(app._content["positions"])
        return app.flashing, any(" on #" in style for _, style in spans)

    assert _run(_fixture(), fake, probe) == ({}, False)


# ----------------------------------------------------------------------
# Hard rule: no ledger write
# ----------------------------------------------------------------------


def test_ticks_never_write_the_ledger(tmp_path: Path) -> None:
    """Three polls over a real ledger leave its bytes and its modification time unchanged."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> tuple[int, str]:
        for _ in range(2):
            await _advance(app, pilot, clock, 10)
        return len(fake.calls), app.plain_text("positions").splitlines()[0]

    model = LedgerReadModel(path, clock=lambda: _FROZEN)
    calls, header = _run(model, fake, probe)
    assert (calls, header.endswith("● live")) == (9, True)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


# ----------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _svg(size: tuple[int, int]) -> str:
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> str:
        fake.set(_SECOND)
        await _advance(app, pilot, clock, 10)
        return capture(app)

    return _run(_fixture(), fake, probe, size=size)


@pytest.mark.parametrize("size", _SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_svg_snapshot_live(size: tuple[int, int], snapshot: SnapshotAssertion) -> None:
    """The reviewed rendering with two fake ticks at each breakpoint."""
    assert_view(snapshot, _svg(size), record=size == _RECORD_SIZE)


# ----------------------------------------------------------------------
# Meters at live prices (issue #1162)
# ----------------------------------------------------------------------


def _booked_meters() -> str:
    """Return the meters of the fixture with no ticker: the booked figures."""

    async def drive() -> str:
        app = HomeApp(
            _fixture(),
            clock=FakeClock(_FROZEN),
            motion=False,
            refresh_seconds=3600,
            exposure_cap=_CAP,
        )
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            return app.plain_text("portfolio-meters")

    return asyncio.run(drive())


def test_invested_and_exposure_meters_follow_the_live_prices() -> None:
    """Live prices move the invested figure and the exposure share; the cash figure stays booked."""
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> str:
        del pilot, clock
        return app.plain_text("portfolio-meters")

    live = _run(_fixture(), fake, probe)
    assert (
        _line_of(live, "cash").split()[-2:],
        _line_of(live, "invested").split()[-2:],
        _line_of(live, "exposure").endswith("23.5% of 15% cap"),
    ) == (["€790.36", "77.1%"], ["€235.14", "22.9%"], True)


def test_the_meters_go_back_to_booked_when_the_layer_pauses() -> None:
    """After 61 s with no good tick the meters read the booked figures again."""
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: FakeClock) -> str:
        fake.fail = {"BTC", "ETH", "SOL"}
        for _ in range(6):
            await _advance(app, pilot, clock, 10)
        await _advance(app, pilot, clock, 1)
        return app.plain_text("portfolio-meters")

    assert _run(_fixture(), fake, probe) == _booked_meters()


def _line_of(text: str, starts: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(starts))
