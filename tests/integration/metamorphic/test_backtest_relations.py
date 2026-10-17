"""Metamorphic relations between backtest runs on seeded synthetic paths (issue #845).

Each test runs the production replay twice (or more) on inputs related in a
known way and checks that the results are related the same way. The paths
come from :mod:`.paths`: seed 0 gives 16 trades in its 90-day window, so every
relation is checked against real entries, stop exits and fees, not an empty
run.

Relations:

1. The same seed twice gives a byte-identical ledger.
2. A 90-day run equals 45 + 45 days with a serve-style restart between.
3. Adding a pair that never signals changes nothing for the other pairs.
4. Reordering the pairs in the config changes nothing.
5. Shifting a path by whole days shifts every trade by the same time.
6. Scaling every price and the capital by k scales cash, fees and P&L by k
   and leaves base quantities as they are.
7. Lookahead: cutting the data after a bar changes no decision up to it.
8. Warm-up: more start-up bars than the strategy buffers change nothing,
   and the configured floor of 300 daily bars decides what a longer warm-up
   decides on a path built to sit on the EMA(200) regime edge (issue #880).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.application.backtest.replay import BacktestRun
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from tests.integration.metamorphic import paths
from tests.integration.metamorphic.paths import BTC, ETH, SOL, backtest, path, trade_rows
from tests.support.fill_kernel import STARTING_CASH

pytestmark = pytest.mark.integration

_SEED = 0
_EXPECTED_TRADES = 16


@pytest.fixture(scope="module")
def base() -> BacktestRun:
    """Run seed 0 on one pair: the run most relations compare against."""
    run = backtest(path(_SEED))
    assert len(run.trades) == _EXPECTED_TRADES, "the seed must trade, or nothing is compared"
    return run


def _equity_by_close(run: BacktestRun) -> dict[datetime, Decimal]:
    """Equity at each 1d close; with more pairs the last 1d bar of that close wins."""
    return dict(run.daily_equity)


def _decided(run: BacktestRun) -> tuple[object, ...]:
    """Everything a run decided: trades, declines, rejections and signals seen."""
    return (trade_rows(run), dict(run.declined), len(run.rejections), run.signals_seen)


def test_the_same_seed_twice_gives_a_byte_identical_ledger(base: BacktestRun) -> None:
    """Relation 1: two runs of one seed write the same ledger bytes.

    Only the wall-clock write stamps are left out of the dump (see
    ``paths.ledger_bytes``): a run that reads the clock, a random source or
    set order while deciding would differ in the trade rows themselves.
    """
    again = backtest(path(_SEED))
    assert paths.ledger_bytes(again.trades) == paths.ledger_bytes(base.trades)


def _first_midnight_holding(run: BacktestRun) -> datetime:
    entry = next(t.occurred_at for t in run.trades if t.side is TradeSide.BUY)
    return datetime.combine(entry.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)


@pytest.mark.parametrize("restart", ["day-45", "holding"])
def test_ninety_days_equal_forty_five_plus_forty_five_with_a_restart(
    base: BacktestRun, restart: str
) -> None:
    """Relation 2: a restart between two legs changes no trade and no daily equity.

    The second leg keeps nothing in memory: the portfolio is rebuilt from the
    first leg's trades and a fresh strategy is seeded from the last 300 bars
    of each interval with the held position and its stop, as serve boots.
    ``day-45`` splits the window in half, as issue #845 asks; ``holding``
    restarts on the first midnight with a position open, so the stop and the
    entry carry over too.
    """
    restart_at = (
        paths.WINDOW_START + timedelta(days=45)
        if restart == "day-45"
        else _first_midnight_holding(base)
    )
    split = paths.backtest_with_restart(path(_SEED), restart_at=restart_at)
    assert (trade_rows(split), split.daily_equity) == (trade_rows(base), base.daily_equity)


@pytest.fixture(scope="module")
def three_pairs() -> BacktestRun:
    """Run seed 0 on BTC and ETH, with SOL flat at 100 so it never signals."""
    return backtest(path(_SEED, pairs=(BTC, ETH), flat=(SOL,)), pairs=(BTC, ETH, SOL))


def test_adding_a_pair_that_never_signals_changes_nothing_for_the_others(
    three_pairs: BacktestRun,
) -> None:
    """Relation 3: BTC and ETH trade the same with or without a silent SOL beside them.

    The pairs share one portfolio, so a pair that took cash or exposure would
    change the others' sizes. SOL's own trade list is empty, and the equity
    at every daily close is the same.
    """
    two = backtest(path(_SEED, pairs=(BTC, ETH)), pairs=(BTC, ETH))
    assert len(trade_rows(two, ETH)) > 0
    assert (
        trade_rows(three_pairs, BTC),
        trade_rows(three_pairs, ETH),
        trade_rows(three_pairs, SOL),
        _equity_by_close(three_pairs),
    ) == (trade_rows(two, BTC), trade_rows(two, ETH), [], _equity_by_close(two))


def test_reordering_the_pairs_in_the_config_changes_nothing(three_pairs: BacktestRun) -> None:
    """Relation 4: the config order of the pairs decides neither delivery nor trades.

    The bars are assembled pair by pair in config order and put in delivery
    order by ``order_for_delivery``, as serve does; one strategy per pair is
    built in config order. A delivery that followed the config order would
    change which of two same-bar entries meets the exposure cap first.
    """
    reordered = backtest(path(_SEED, pairs=(ETH, BTC), flat=(SOL,)), pairs=(SOL, ETH, BTC))
    assert _decided(reordered) == _decided(three_pairs)
    assert reordered.daily_equity == three_pairs.daily_equity


@pytest.mark.parametrize("days", [1, 365])
def test_shifting_a_path_by_whole_days_shifts_every_trade_by_the_same_time(
    base: BacktestRun, days: int
) -> None:
    """Relation 5: the same bars a whole number of days later give the same trades that much later.

    The shift is whole days, six 4h bars each: a shift by fewer bars would
    regroup the 4h bars into other UTC days, so the 1d candles, and with them
    the EMA regime, would be different data rather than the same path later.
    """
    by = timedelta(days=days)
    later = backtest(
        paths.shifted(path(_SEED), by),
        window_start=paths.WINDOW_START + by,
        window_end=paths.WINDOW_END + by,
    )
    assert [(*row[:5], row[5] - by) for row in trade_rows(later)] == trade_rows(base)


def _relative_gap(scaled: Decimal, plain: Decimal, k: Decimal) -> Decimal:
    return abs(scaled - plain * k) / abs(plain * k)


@pytest.mark.parametrize("k", [Decimal("0.001"), Decimal(3)], ids=str)
def test_scaling_prices_and_capital_by_k_scales_money_by_k_and_keeps_quantities(
    base: BacktestRun, k: Decimal
) -> None:
    """Relation 6: k times every price and the capital gives k times every amount of money.

    Sizing is ``capital x pct / price``, so base quantities do not move at
    all; prices, fees, cash and equity move by exactly k up to the 28-digit
    decimal context, which rounds a division of k-scaled values at the 28th
    significant digit (relative error under 1e-24 here). The backtest path
    has no lot step and no minimum notional: sizing rounds only at that
    precision and the walk fill model takes any size, so k = 0.001 (a 0.25
    USDT position) is as meaningful as k = 3.
    """
    tolerance = Decimal("1e-24")
    scaled = backtest(paths.scaled(path(_SEED), k), capital=STARTING_CASH * k)
    plain_rows, scaled_rows = trade_rows(base), trade_rows(scaled)
    assert [(r[0], r[1], r[2], r[5]) for r in scaled_rows] == [
        (r[0], r[1], r[2], r[5]) for r in plain_rows
    ]
    gaps = [
        gap
        for s, p in zip(scaled_rows, plain_rows, strict=True)
        for gap in (_relative_gap(s[3], p[3], k), _relative_gap(s[4], p[4], k))
    ]
    gaps += [
        _relative_gap(s, p, k)
        for (_, s), (_, p) in zip(scaled.daily_equity, base.daily_equity, strict=True)
    ]
    assert [g for g in gaps if g > tolerance] == []


def _cut_points(run: BacktestRun) -> dict[str, datetime]:
    entries = [t.occurred_at for t in run.trades if t.side is TradeSide.BUY]
    exits = [t.occurred_at for t in run.trades if t.side is TradeSide.SELL]
    return {
        "first-entry-bar": entries[0],
        "middle-exit-bar": exits[len(exits) // 2],
        "bar-before-last-exit": exits[-1] - timedelta(hours=4),
    }


@pytest.mark.parametrize("cut", ["first-entry-bar", "middle-exit-bar", "bar-before-last-exit"])
def test_cutting_the_data_after_a_bar_changes_no_decision_up_to_it(
    base: BacktestRun, cut: str
) -> None:
    """Relation 7 (lookahead): bars after t never change a trade or an equity mark up to t.

    The data is cut right after the bar that closes at t, as a feed that has
    delivered up to t would hold it. The cut points sit on an entry bar, on
    an exit bar and one bar before an exit, where a strategy peeking one bar
    ahead would act differently.
    """
    t = _cut_points(base)[cut]
    cut_run = backtest([b for b in path(_SEED) if b.close_time <= t])
    assert (trade_rows(cut_run), cut_run.daily_equity) == (
        [row for row in trade_rows(base) if row[5] <= t],
        [(at, e) for at, e in base.daily_equity if at <= t],
    )


@pytest.fixture(scope="module")
def long_warm_up() -> BacktestRun:
    """Run seed 0 with 400 days of warm-up, more than any indicator or buffer reads."""
    return backtest(path(_SEED, warm_up_days=400))


def _daily_buffer() -> list[Bar]:
    """300 days of warm-up: what the strategy buffers and serve warms up with."""
    return path(_SEED, warm_up_days=300)


def _h4_floor() -> list[Bar]:
    """400 days of daily bars but only the 45 4h bars the config floor allows."""
    first = paths.WINDOW_START - timedelta(hours=4 * 45)
    return [
        b
        for b in path(_SEED, warm_up_days=400)
        if b.interval is KlineInterval.D1 or b.open_time >= first
    ]


@pytest.mark.parametrize(
    "shorter", [_daily_buffer, _h4_floor], ids=["1d-buffer-300", "4h-floor-45"]
)
def test_more_warm_up_than_the_indicators_need_changes_no_decision(
    long_warm_up: BacktestRun, shorter: Callable[[], list[Bar]]
) -> None:
    """Relation 8 (warm-up): 400 days of start-up bars decide what the shorter warm-up decides.

    ``1d-buffer-300``: 300 daily bars, what the strategy buffers and serve
    warms up with (``warmup_bars_1d``). ``4h-floor-45``: the configured 4h
    floor (``warmup_bars_4h`` >= 45), with the daily bars in full. Past these
    counts every 4h and 1d indicator reads the same bars.
    """
    assert _decided(backtest(shorter())) == _decided(long_warm_up)


def test_the_configured_floor_of_300_daily_bars_decides_what_400_decide() -> None:
    """Relation 8 (warm-up, #880): on the regime edge, 300 daily bars decide what 400 decide.

    The regime-edge path enters only if the EMA(200) is seeded from the 300
    daily closes the strategy buffers; 200 would leave the regime off. The
    300-bar run is the default path moved 100 days later, so both runs share
    one window and one climb, and the longer run only adds calm days in front.
    """
    window = paths.REGIME_EDGE_WINDOW + timedelta(days=100)
    floor = paths.shifted(paths.regime_edge(), timedelta(days=100))
    longer = paths.regime_edge(calm_days=200)
    daily_before = [b for b in floor if b.interval is KlineInterval.D1 and b.close_time <= window]
    floor_run = backtest(floor, window_start=window)

    assert (len(daily_before), floor_run.trades[0].side, floor_run.trades[0].occurred_at) == (
        300,
        TradeSide.BUY,
        datetime(2022, 2, 9, 7, 59, 59, 999000, tzinfo=UTC),
    )
    assert _decided(floor_run) == _decided(backtest(longer, window_start=window))
