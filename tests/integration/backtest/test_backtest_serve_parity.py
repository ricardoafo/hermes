"""Integration test: the backtest path and the serve path decide the same on the same bars.

One generated BTC/USDT series (flat, a rise that trips the entry, a fall that
trips the chandelier stop) goes through two real paths:

- backtest: ``replay`` over a ``FillKernel`` composed as
  ``scripts/backtest/run_portfolio_backtest.py`` composes it;
- serve: ``serve.run`` over a real ``build_composition`` (``ShadowExecutor``, the
  shadow risk overlay, a real SQLite ledger), fed by a scripted feed.

Both run the default ``SwingStrategyAdapter`` and the signed sizing and fees.
This is not the replay parity reader (#798), which compares replay against
replay; here the second side is the live loop.

Invariants (issue #692):

- both paths emit the same signals (kind, time, prices) on the same bars;
- both paths fill the same trades (side, qty, price, time);
- the series yields one entry and one exit, so the equality is not vacuous.

Seams: ``_ScriptedFeed`` stands in for the Binance live klines endpoint
(tests/integration/entrypoints/test_serve_bar_loop.py drives the same
feed shape); ``UnavailableBooks`` stands in for the Binance depth endpoint, so
serve fills on the pre-registration section 6.2 book as the backtest does.
"""

import asyncio
import dataclasses
import json
import sqlite3
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.backtest.replay import replay
from hermes.application.execution.kernel import FillKernel
from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillTriggerEvaluator
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.fees.fee_schedule_selector import select_fee_schedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of, delivery_key
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.config.loader import load_config
from tests.support.serve_loop import (
    BTC,
    PROJECT_ROOT,
    FakeClock,
    FakeFeed,
    build,
    drive,
    events,
)

pytestmark = pytest.mark.integration

_T0 = datetime(2023, 1, 1, tzinfo=UTC)
_DAYS = 260
_WINDOW_START = _T0 + timedelta(days=210)
_WINDOW_END = _T0 + timedelta(days=_DAYS)
_FLAT_DAYS = 215
_RISE_DAYS = 30
_ONE = Decimal("100")


def _daily_close(day: int) -> Decimal:
    """Flat at 100, then +1%/day for 30 days, then -4%/day."""
    if day < _FLAT_DAYS:
        return _ONE
    if day < _FLAT_DAYS + _RISE_DAYS:
        return _ONE * Decimal("1.01") ** (day - _FLAT_DAYS + 1)
    peak = _ONE * Decimal("1.01") ** _RISE_DAYS
    return peak * Decimal("0.96") ** (day - _FLAT_DAYS - _RISE_DAYS + 1)


def _bars() -> list[Bar]:
    """Six 4h bars per day interpolating the daily move, plus the 1d bar, in delivery order.

    Close times are Binance's inclusive value (``open + interval - 1 ms``), the
    one the live feed delivers and the Vision archives carry.
    """
    bars: list[Bar] = []
    previous = _ONE
    for day in range(_DAYS):
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
                    pair=BTC,
                    interval=KlineInterval.H4,
                    open_time=opened,
                    open=bar_open,
                    high=high,
                    low=low,
                    close=bar_close,
                    volume=Decimal("1000"),
                    close_time=close_time_of(opened, KlineInterval.H4),
                )
            )
        opened = _T0 + timedelta(days=day)
        bars.append(
            Bar(
                pair=BTC,
                interval=KlineInterval.D1,
                open_time=opened,
                open=previous,
                high=max(highs),
                low=min(lows),
                close=close,
                volume=Decimal("6000"),
                close_time=close_time_of(opened, KlineInterval.D1),
            )
        )
        previous = close
    return sorted(bars, key=delivery_key)


class _RecordingSwing(SwingStrategyAdapter):
    """The real swing strategy at its default parameters, noting every signal it returns."""

    def __init__(self) -> None:
        """Start with no signals seen."""
        super().__init__()
        self.signals: list[StrategySignal] = []

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Delegate to the real strategy and record what it returned."""
        signal = super().on_bar(bar, portfolio_state)
        if signal is not None:
            self.signals.append(signal)
        return signal


class _ScriptedFeed(FakeFeed):
    """Closed-bar feed (seam: the Binance live klines stream) that moves the clock per bar."""

    def __init__(self, warm: Sequence[Bar], live_bars: Sequence[Bar], clock: FakeClock) -> None:
        """Script the feed and bind the clock it advances."""
        super().__init__(warm=warm, live_bars=live_bars)
        self._clock = clock

    async def stream_closed_bars(
        self, pairs: object, intervals: object, *, resume_after: object = None
    ) -> AsyncIterator[Bar]:
        """Yield each live bar with the clock at its close, then idle like a live feed."""
        for bar in self.live_bars:
            self._clock.current = bar.close_time
            yield bar
        self.drained.set()
        await asyncio.Event().wait()


@dataclasses.dataclass(frozen=True, slots=True)
class _Decision:
    """What a path decided: a signal's identity, or a fill's side, qty, price and time."""

    what: str
    time: datetime
    detail: tuple[object, ...]


def _signal_decisions(signals: Sequence[StrategySignal]) -> list[_Decision]:
    """Return the identity of each signal: kind, time, entry price, stop, ATR and reason."""
    return [
        _Decision(
            s.kind.value,
            s.signal_time,
            (s.entry_price, s.stop_price, s.atr_context, s.exit_reason),
        )
        for s in signals
    ]


def _fill_decisions(trades: Sequence[TradeExecuted]) -> list[_Decision]:
    """Return side, qty, price and fee of each trade at its time."""
    return [_Decision(t.side.value, t.occurred_at, (t.qty, t.price, t.fee)) for t in trades]


@dataclasses.dataclass(frozen=True, slots=True)
class _PathResult:
    """What one path produced from the shared bars."""

    signals: list[StrategySignal]
    trades: list[TradeExecuted]


def _backtest(bars: list[Bar]) -> _PathResult:
    """Replay ``bars`` the way the backtest script composes it; return the strategy and trades."""
    config = load_config(PROJECT_ROOT / "config", env={})
    roster = next(s for s in config.strategies.strategies if s.enabled)
    strategy_id = StrategyId(roster.name)
    fee_rows = load_binance_fee_history(
        PROJECT_ROOT / "docs/experiments/swing-mvp1/fee-history.csv"
    )
    kernel = FillKernel(
        fill_model=OrderbookWalkFillModel(),
        risk_engine=RiskEngine(rules=[CorrelationLock()]),
        kill_evaluator=KillTriggerEvaluator(pre_reg=PreRegistration()),
        tracker=PortfolioTracker(
            PortfolioSnapshot.seed(
                strategy_id, Money(amount=roster.capital_budget, currency=roster.quote)
            )
        ),
        strategy_id=strategy_id,
        sizing=roster.sizing_policy(),
    )
    strategy = _RecordingSwing()
    ids = iter(f"01HZ{i:022d}" for i in range(100_000))

    def fees_at(at: datetime, pair: Pair) -> FeeSchedule:
        return select_fee_schedule(fee_rows, at, pair)

    run = replay(
        bars,
        strategy=strategy,
        kernel=kernel,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        fees_at=fees_at,
        correlation_id=lambda: next(ids),
    )
    assert run.signals_seen == len(strategy.signals)
    return _PathResult(signals=strategy.signals, trades=run.trades)


def _serve(bars: list[Bar], db: Path) -> tuple[_PathResult, list[tuple[str, str]]]:
    """Feed ``bars`` through serve: history up to the window as warm-up, the rest live.

    Returns what serve decided, and the ``(kind, occurred_at)`` of each
    SignalEmitted row it wrote to its ledger.
    """
    warm = [b for b in bars if b.close_time <= _WINDOW_START]
    live_bars = [b for b in bars if b.close_time > _WINDOW_START]
    clock = FakeClock(_T0 - timedelta(days=1))
    feed = _ScriptedFeed(warm, live_bars, clock)
    strategy = _RecordingSwing()
    composition = dataclasses.replace(
        build(db, feed, clock.current, clock=clock), strategies={BTC: strategy}
    )
    strategy_id = composition.strategy_id
    assert asyncio.run(drive(composition, feed, timeout=120.0)) == 0
    connection = sqlite3.connect(db)
    try:
        trades = list(SqliteLedger(connection).trade_events(strategy_id))
    finally:
        connection.close()
    rows = [
        (json.loads(r["payload_json"])["kind"], r["occurred_at"])
        for r in events(db, "SignalEmitted")
    ]
    return _PathResult(signals=strategy.signals, trades=trades), rows


@pytest.fixture(scope="module")
def both_paths(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[_PathResult, _PathResult, list[tuple[str, str]]]:
    """Run the backtest and the serve path once over the same generated bars."""
    bars = _bars()
    served, rows = _serve(bars, tmp_path_factory.mktemp("serve") / "ledger.sqlite3")
    return _backtest(bars), served, rows


def test_the_series_produces_one_entry_and_one_exit_on_both_paths(
    both_paths: tuple[_PathResult, _PathResult, list[tuple[str, str]]],
) -> None:
    """The comparison is not vacuous: the signed strategy enters once and exits once, on both."""
    backtest, served, rows = both_paths
    assert [s.kind.value for s in backtest.signals] == ["entry", "exit"]
    assert [s.kind.value for s in served.signals] == ["entry", "exit"]
    assert [t.side.value for t in backtest.trades] == ["BUY", "SELL"]
    assert [t.side.value for t in served.trades] == ["BUY", "SELL"]
    assert rows == [
        ("entry", "2023-08-04T23:59:59.999000+00:00"),
        ("exit", "2023-09-03T07:59:59.999000+00:00"),
    ]


def test_backtest_and_serve_emit_identical_signals_on_the_same_bars(
    both_paths: tuple[_PathResult, _PathResult, list[tuple[str, str]]],
) -> None:
    """Same bars, same default strategy: same signal kinds, times, prices, stop and ATR."""
    backtest, served, _ = both_paths
    assert _signal_decisions(served.signals) == _signal_decisions(backtest.signals)


def test_backtest_and_serve_fill_identical_trades_on_the_same_bars(
    both_paths: tuple[_PathResult, _PathResult, list[tuple[str, str]]],
) -> None:
    """Same signals: the same fills, side, qty, price, fee and time, on both paths."""
    backtest, served, _ = both_paths
    assert _fill_decisions(served.trades) == _fill_decisions(backtest.trades)
