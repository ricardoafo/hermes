"""Integration test: a backtest runs the real fill path end to end.

Real ``SwingStrategyAdapter``, real ``OrderRouter`` (inside ``FillKernel``),
real ``OrderbookWalkFillModel``, real ``PortfolioTracker``, real
``CalibrationHarness`` and a SQLite ledger; nothing is mocked. The bars are
generated: flat for 215 days (no breakout, so no warm-up entry), a 1%/day
rise that trips the Donchian + ADX + EMA(200) entry, then a 4%/day fall that
trips the chandelier stop.

Invariants (backtest-driver-mvp1-phase2 change.md R-1.2, R-2, A-4.4):

- The run fills one entry and one exit, so M-02 is non-zero: the phase 2
  acceptance signal (phase 1 always read 0).
- The entry fills at close * 1.0005 and the stop exit at stop * 0.9985, so
  M-02 averages exactly 10 bps.
- Persisting the kernel's trades to the ledger and rebuilding the tracker
  from them reproduces the backtest's cash, realized PnL and positions.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.backtest.replay import BacktestRun, replay
from hermes.application.calibration.harness import CalibrationHarness
from hermes.application.execution.kernel import FillKernel
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.trading.order import OrderSide
from tests.support.fill_kernel import BTC_USDT, STRATEGY, fees_at, kernel, seed

pytestmark = pytest.mark.integration

_T0 = datetime(2023, 1, 1, tzinfo=UTC)
_WINDOW_START = _T0 + timedelta(days=210)
_WINDOW_END = _T0 + timedelta(days=260)
_FLAT_DAYS = 215
_RISE_DAYS = 30
_BPS = Decimal("10000")


def _daily_close(day: int) -> Decimal:
    """Flat at 100, then +1%/day for 30 days, then -4%/day."""
    if day < _FLAT_DAYS:
        return Decimal("100")
    if day < _FLAT_DAYS + _RISE_DAYS:
        return Decimal("100") * Decimal("1.01") ** (day - _FLAT_DAYS + 1)
    peak = Decimal("100") * Decimal("1.01") ** _RISE_DAYS
    return peak * Decimal("0.96") ** (day - _FLAT_DAYS - _RISE_DAYS + 1)


def _bars(days: int = 260) -> list[Bar]:
    """Six 4h bars per day interpolating the daily move, plus the 1D bar, in delivery order."""
    bars: list[Bar] = []
    previous = Decimal("100")
    for day in range(days):
        close = _daily_close(day)
        points = [previous + (close - previous) * Decimal(i) / 6 for i in range(7)]
        highs: list[Decimal] = []
        lows: list[Decimal] = []
        for i in range(6):
            bar_open, bar_close = points[i], points[i + 1]
            high = max(bar_open, bar_close) * Decimal("1.001")
            low = min(bar_open, bar_close) * Decimal("0.999")
            highs.append(high)
            lows.append(low)
            opened = _T0 + timedelta(days=day, hours=4 * i)
            bars.append(
                Bar(
                    pair=BTC_USDT,
                    interval=KlineInterval.H4,
                    open_time=opened,
                    open=bar_open,
                    high=high,
                    low=low,
                    close=bar_close,
                    volume=Decimal("1000"),
                    close_time=opened + timedelta(hours=4),
                )
            )
        opened = _T0 + timedelta(days=day)
        bars.append(
            Bar(
                pair=BTC_USDT,
                interval=KlineInterval.D1,
                open_time=opened,
                open=previous,
                high=max(highs),
                low=min(lows),
                close=close,
                volume=Decimal("6000"),
                close_time=opened + timedelta(days=1),
            )
        )
        previous = close
    return sorted(bars, key=lambda b: (b.close_time, b.interval != KlineInterval.D1))


def _replay(k: FillKernel) -> BacktestRun:
    ids = iter(f"01HZ{i:022d}" for i in range(10_000))
    return replay(
        _bars(),
        strategy=SwingStrategyAdapter(),
        kernel=k,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        fees_at=fees_at,
        correlation_id=lambda: next(ids),
    )


def test_backtest_fills_through_the_real_path_with_non_zero_slippage() -> None:
    """One entry and one stop exit fill; M-01 = 2 and M-02 averages 10 bps, not 0."""
    run = _replay(kernel())
    assert [f.order.side for f in run.fills] == [OrderSide.BUY, OrderSide.SELL]
    assert (run.rejections, dict(run.declined)) == ([], {})
    report = CalibrationHarness(strategy_id=STRATEGY.value).run(
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        fills=run.fills,
        rejections=run.rejections,
        mid_prices=run.reference_prices,
    )
    assert report.m01_fill_count == 2
    assert report.m02_fill_price_delta_bps.avg_bps > 0
    assert report.m02_fill_price_delta_bps.avg_bps == Decimal("10")


def test_fills_sit_at_the_pre_registered_prices() -> None:
    """Entry = signal close * 1.0005; chandelier exit = stop * 0.9985 (pre-reg §6.2)."""
    run = _replay(kernel())
    entry, exit_ = run.fills
    entry_close, stop = run.reference_prices
    assert entry.avg_price == entry_close * (1 + Decimal("5") / _BPS)
    assert abs(exit_.avg_price - stop * (1 - Decimal("15") / _BPS)) < Decimal("1e-20")
    assert exit_.executed_qty == entry.executed_qty


def test_ledger_round_trip_rebuilds_the_backtest_portfolio() -> None:
    """Trades appended to SQLite and replayed by PortfolioTracker.rebuilt match the live run."""
    k = kernel()
    run = _replay(k)
    connection = sqlite3.connect(":memory:")
    try:
        ledger = SqliteLedger(connection)
        for trade in run.trades:
            ledger.append_event(trade)
        rebuilt = PortfolioTracker.rebuilt(seed(), ledger.trade_events(STRATEGY)).unwrap()
    finally:
        connection.close()
    live = k.tracker.snapshot
    replayed = rebuilt.snapshot
    assert len(run.trades) == 2
    assert (replayed.cash, replayed.realized_pnl, dict(replayed.positions)) == (
        live.cash,
        live.realized_pnl,
        dict(live.positions),
    )
