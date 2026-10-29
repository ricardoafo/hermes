"""The System tab shows the live USDT/EUR quote beside the ECB rate (#1330).

Invariants:

- beside "ECB rate of <day>" the EUR rate row shows "live <rate>" in euro per
  USDT with four decimals; once the quote is older than 90 s it is dimmed and
  carries its age; without a quote the row is as before;
- the live quote never replaces the ECB date the booked figures use;
- the status line's "checked <age> ago" follows the console clock: two
  minutes on, the age is two minutes more.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hermes.adapters.console import FixtureReadModel
from hermes.application.console.live_rate import LiveRate
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_system import timing_text
from hermes.entrypoints.tui_theme import BASE
from tests.support.clock import FakeClock

_NOW = datetime(2026, 10, 19, 14, 20, tzinfo=UTC)
_FIXTURE_NOW = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)  # 14:12 Madrid, the fixture's read time


def _timing(live: LiveRate | None) -> str:
    return timing_text(
        None,
        eur_rate_date=date(2026, 10, 19),
        bar_seconds=(),
        now=_NOW,
        tz=_NOW.tzinfo or UTC,
        tokens=BASE,
        live_rate=live,
    ).plain.splitlines()[2]


def _live(age: int) -> LiveRate:
    return LiveRate(
        Decimal("0.89085"), _NOW - timedelta(seconds=age), timedelta(seconds=age), age <= 90
    )


def test_a_fresh_live_quote_sits_beside_the_ecb_date() -> None:
    """ECB date kept; the live quote 0.89085 reads 'live 0.8908' to four decimals (half even)."""
    assert _timing(_live(5)) == "EUR rate       ECB rate of 2026-10-19 · live 0.8908"


def test_a_stale_live_quote_carries_its_age() -> None:
    """A quote 3 min old reads 'live 0.8908 · 3 min ago' in the muted token."""
    assert _timing(_live(180)) == "EUR rate       ECB rate of 2026-10-19 · live 0.8908 · 3 min ago"


def test_without_a_live_quote_the_row_is_as_before() -> None:
    """No quote yet: only the ECB date."""
    assert _timing(None) == "EUR rate       ECB rate of 2026-10-19"


class _Ticker:
    """Prices everything at 126 / 127; the USDT/EUR pair too, or fails when told to."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def get_ticker(self, pair: Pair) -> Ticker:
        if self.fail:
            raise KrakenPublicError("down")
        return Ticker(pair, Decimal(126), Decimal(127), Decimal(126), _NOW)


def _rate_row(ticker: _Ticker) -> str:
    async def drive() -> str:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=FakeClock(_NOW),
            motion=False,
            refresh_seconds=3600,
            exposure_cap=Decimal("0.15"),
            ticker=ticker,
        )
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("8")
            await pilot.pause()
            return next(
                line for line in app.plain_text("sys-timing").splitlines() if "EUR rate" in line
            )

    return asyncio.run(drive())


def test_the_system_tab_shows_the_quote_the_ticker_gave() -> None:
    """A ticker answering 126 / 127 for USDT/EUR: the middle, 126.5, shows next to the ECB date."""
    assert _rate_row(_Ticker()).endswith("· live 126.5000")


def test_a_failing_ticker_leaves_the_ecb_rate_alone() -> None:
    """The quote fails: the row is the fixture's own (no ECB date yet), no live part, no error."""
    assert _rate_row(_Ticker(fail=True)) == "EUR rate       not available yet"


def test_the_status_line_age_follows_the_console_clock() -> None:
    """The fixture's last check is 12 min before its read time; 2 min on the clock, 14 min."""

    async def drive() -> tuple[str, str]:
        clock = FakeClock(_FIXTURE_NOW)
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=clock,
            motion=False,
            refresh_seconds=3600,
            exposure_cap=Decimal("0.15"),
        )
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            before = app.plain_text("heartbeat").strip()
            clock.advance(timedelta(minutes=2))
            await pilot.pause(1.2)
            return before, app.plain_text("heartbeat").strip()

    assert asyncio.run(drive()) == (
        "● live · checked 12m ago · next 18:00",
        "● live · checked 14m ago · next 18:00",
    )
