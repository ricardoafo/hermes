"""Tests for the live-phase risk inputs serve feeds with real money (#1158).

Invariants:

- RO-02's median is the median of the pair's spread samples in the hour
  before ``now``: older samples and other pairs never count, and no sample
  gives ``None``.
- The stablecoin watch returns its latest reading only while it is at most
  ``max_age`` old; a failed poll is skipped and keeps nothing new.
- The recovery days count from the last equity high, and only once the
  drawdown since it reached §10's 8%; below that the kill reads 0.0.

The stores are a real in-memory SQLite ledger; only the price fetch and the
clock are scripted. The Kraken seam is covered by
``tests/unit/adapters/exchanges/test_kraken_public.py`` (``TestGetUsdPrice``).
"""

import asyncio
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.clock import SimulatedClock
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.live_risk_inputs import (
    STABLE_PRICE_MAX_AGE,
    LedgerLiveRiskInputs,
    StablePriceWatch,
)
from hermes.domain.errors import NetworkError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_STRATEGY = StrategyId("swing-donchian-4h")
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
_SEED = PortfolioSnapshot.seed(_STRATEGY, Money(amount=Decimal(10000), currency=Currency.USDT))


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Return a ledger over an in-memory SQLite connection, closed afterwards."""
    conn = sqlite3.connect(":memory:")
    yield SqliteLedger(conn)
    conn.close()


def _watch(prices: list[Decimal | Exception], clock: SimulatedClock) -> StablePriceWatch:
    """Return a watch whose fetch returns, or raises, ``prices`` in order."""
    script = iter(prices)

    def fetch() -> Decimal:
        item = next(script)
        if isinstance(item, Exception):
            raise item
        return item

    return StablePriceWatch(fetch=fetch, clock=clock, interval_seconds=0.01)


def _inputs(ledger: SqliteLedger, watch: StablePriceWatch | None = None) -> LedgerLiveRiskInputs:
    """Return the live inputs over ``ledger``."""
    stable = watch if watch is not None else _watch([], SimulatedClock(start=_NOW))
    return LedgerLiveRiskInputs(spreads=ledger, marks=ledger, strategy_id=_STRATEGY, stable=stable)


def _sample(pair: Pair, at: datetime, spread: str) -> SpreadSample:
    """Return a spread sample of ``pair`` read at ``at``."""
    return SpreadSample(
        pair=pair,
        sampled_at=at,
        book_time=at,
        spread=Decimal(spread),
        median_spread_1h=Decimal(spread),
        samples_1h=1,
    )


def _mark(at: datetime, equity: int, peak: int) -> PortfolioMark:
    """Return a mark at ``at`` with ``equity`` in cash under a ``peak`` high."""
    snapshot = replace(_SEED, cash=Decimal(equity), peak_equity=Decimal(peak))
    return PortfolioMark(snapshot=snapshot, pair=_BTC, interval=KlineInterval.H4, bar_close_time=at)


def test_median_covers_only_the_pairs_samples_of_the_last_hour(ledger: SqliteLedger) -> None:
    """Samples older than an hour and another pair's samples never move the median."""
    for at, spread in (
        (_NOW - timedelta(minutes=70), "0.9"),
        (_NOW - timedelta(minutes=50), "0.0001"),
        (_NOW - timedelta(minutes=30), "0.0003"),
        (_NOW - timedelta(minutes=10), "0.0002"),
    ):
        ledger.record_spread_sample(_sample(_BTC, at, spread))
    ledger.record_spread_sample(_sample(_ETH, _NOW - timedelta(minutes=5), "0.5"))

    assert _inputs(ledger).median_spread_1h(_BTC, _NOW) == Decimal("0.0002")


def test_no_sample_in_the_hour_gives_no_median(ledger: SqliteLedger) -> None:
    """Without a recent sample the median is ``None``, never a made-up value."""
    ledger.record_spread_sample(_sample(_BTC, _NOW - timedelta(hours=2), "0.0001"))
    inputs = _inputs(ledger)

    assert (
        inputs.median_spread_1h(_BTC, _NOW),
        inputs.median_spread_1h(_BTC, _NOW - timedelta(hours=1, minutes=30)),
    ) == (None, Decimal("0.0001"))


def test_a_polled_price_is_returned_while_it_is_fresh() -> None:
    """The latest reading is returned up to ``max_age`` after it was asked for."""
    clock = SimulatedClock(start=_NOW)
    watch = _watch([Decimal("0.9992")], clock)

    stored = asyncio.run(watch.poll_once())

    assert (stored, watch.price(_NOW + STABLE_PRICE_MAX_AGE)) == (True, Decimal("0.9992"))


def test_a_reading_older_than_max_age_is_no_price() -> None:
    """Past ``max_age`` the depeg rules get ``None``, not a stale price."""
    clock = SimulatedClock(start=_NOW)
    watch = _watch([Decimal("0.9992")], clock)
    asyncio.run(watch.poll_once())

    assert (
        watch.price(_NOW + STABLE_PRICE_MAX_AGE),
        watch.price(_NOW + STABLE_PRICE_MAX_AGE + timedelta(seconds=1)),
    ) == (Decimal("0.9992"), None)


def test_a_failed_poll_keeps_the_earlier_reading_and_its_age() -> None:
    """An outage stores nothing: the earlier price ages out on its own time."""
    clock = SimulatedClock(start=_NOW)
    watch = _watch([Decimal("1.0001"), NetworkError("ticker down")], clock)
    asyncio.run(watch.poll_once())
    clock.set(_NOW + timedelta(minutes=1))

    stored = asyncio.run(watch.poll_once())

    assert (stored, watch.price(_NOW + timedelta(minutes=1))) == (False, Decimal("1.0001"))
    assert watch.price(_NOW + STABLE_PRICE_MAX_AGE + timedelta(seconds=1)) is None


def test_no_poll_yet_is_no_price() -> None:
    """Before the first reading the depeg rules get ``None``; the first poll fills it."""
    watch = _watch([Decimal("0.9998")], SimulatedClock(start=_NOW))
    before = watch.price(_NOW)
    asyncio.run(watch.poll_once())

    assert (before, watch.price(_NOW)) == (None, Decimal("0.9998"))


def test_run_polls_at_once_and_stops_on_shutdown() -> None:
    """The first poll runs at start, so entries never wait a step for a price."""
    clock = SimulatedClock(start=_NOW)
    watch = _watch([Decimal("0.9995")] * 1000, clock)

    async def go() -> None:
        shutdown = asyncio.Event()
        task = asyncio.create_task(watch.run(shutdown))
        await asyncio.sleep(0.05)
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(go())

    assert watch.price(_NOW) == Decimal("0.9995")


def test_a_non_positive_step_is_refused() -> None:
    """A zero poll step would spin the event loop; it is refused at build."""
    with pytest.raises(ValueError, match="must be > 0"):
        StablePriceWatch(
            fetch=lambda: Decimal(1), clock=SimulatedClock(start=_NOW), interval_seconds=0
        )


def test_recovery_days_count_from_the_high_after_an_eight_percent_drawdown(
    ledger: SqliteLedger,
) -> None:
    """A high 95.5 days ago and a 10% drawdown since: the kill reads 95.5 days."""
    high_at = _NOW - timedelta(days=95, hours=12)
    ledger.record_portfolio_mark(_mark(high_at, 11000, 11000))
    ledger.record_portfolio_mark(_mark(high_at + timedelta(days=30), 9900, 11000))

    assert _inputs(ledger).days_since_equity_high(_NOW) == 95.5


def test_a_drawdown_under_eight_percent_never_counts_recovery_days(
    ledger: SqliteLedger,
) -> None:
    """§10 counts recovery only from a drawdown of at least 8%: 5% reads 0.0."""
    high_at = _NOW - timedelta(days=120)
    ledger.record_portfolio_mark(_mark(high_at, 11000, 11000))
    ledger.record_portfolio_mark(_mark(high_at + timedelta(days=30), 10450, 11000))

    assert _inputs(ledger).days_since_equity_high(_NOW) == 0.0


def test_no_mark_reads_no_recovery_days(ledger: SqliteLedger) -> None:
    """A ledger without marks has no high to count from."""
    assert _inputs(ledger).days_since_equity_high(_NOW) == 0.0


@given(
    drawdown_pct=st.integers(min_value=0, max_value=50),
    age_hours=st.integers(min_value=1, max_value=24 * 400),
)
def test_recovery_days_are_nonzero_exactly_when_the_drawdown_reaches_eight_percent(
    drawdown_pct: int, age_hours: int
) -> None:
    """For any drawdown and age, days > 0 iff the drawdown since the high is >= 8%."""
    conn = sqlite3.connect(":memory:")
    try:
        ledger = SqliteLedger(conn)
        high_at = _NOW - timedelta(hours=age_hours)
        ledger.record_portfolio_mark(_mark(high_at, 10000, 10000))
        ledger.record_portfolio_mark(
            _mark(high_at + timedelta(minutes=1), 10000 - 100 * drawdown_pct, 10000)
        )

        days = _inputs(ledger).days_since_equity_high(_NOW)
    finally:
        conn.close()

    assert (days > 0) is (drawdown_pct >= 8)
