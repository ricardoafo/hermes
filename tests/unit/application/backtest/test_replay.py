"""Tests for the backtest replay loop and its §6.2 execution books.

Invariants (backtest-driver-mvp1-phase2 change.md D-1, D-4, R-2):

- Signals before the window warm the strategy up and are never executed.
- An entry fills at close * 1.0005; a chandelier exit at stop * 0.9985; a
  time-stop or regime-break exit at close * 0.9995; the reference price is
  recorded as the M-02 mid.
- A zero-volume bar has no book, so its signal is declined as ``no-book``.
- The equity curve holds the marked equity at each in-window 1D close.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from hermes.application.backtest.replay import BacktestRun, ExecutionModel, replay
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.precision import EXACT_CONTEXT
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.pair import Pair
from tests.support.fill_kernel import BTC_USDT, fees, kernel

_WINDOW = datetime(2024, 1, 1, tzinfo=UTC)
_END = datetime(2024, 1, 31, tzinfo=UTC)
_BPS = Decimal("10000")
_PRECISION = Decimal("1e-20")


def _bar(
    close_time: datetime,
    close: Decimal,
    *,
    interval: KlineInterval = KlineInterval.H4,
    volume: Decimal = Decimal("1000"),
) -> Bar:
    span = timedelta(days=1) if interval is KlineInterval.D1 else timedelta(hours=4)
    return Bar(
        pair=BTC_USDT,
        interval=interval,
        open_time=close_time - span,
        open=close,
        high=close * Decimal("1.01"),
        low=close * Decimal("0.99"),
        close=close,
        volume=volume,
        close_time=close_time,
    )


def _entry(at: datetime, price: Decimal) -> StrategySignal:
    return StrategySignal(
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=price,
        stop_price=price * Decimal("0.9"),
        atr_context=Decimal("2"),
        signal_time=at,
    )


def _exit(at: datetime, stop: Decimal, reason: str) -> StrategySignal:
    return StrategySignal(
        kind=SignalKind.EXIT,
        side=SignalSide.LONG,
        entry_price=None,
        stop_price=stop,
        atr_context=Decimal("2"),
        signal_time=at,
        exit_reason=reason,
    )


@dataclass
class _ScriptedStrategy:
    """Emits a preset signal on the bar closing at its time; records the states it saw."""

    script: dict[datetime, StrategySignal]
    seen: list[PortfolioState] = field(default_factory=list)

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        self.seen.append(portfolio_state)
        if bar.interval is not KlineInterval.H4:
            return None
        return self.script.get(bar.close_time)

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        _ = (bars, open_position)


def _fees_at(at: datetime, pair: Pair) -> FeeSchedule:
    _ = at
    return fees(pair)


def _run(bars: list[Bar], script: dict[datetime, StrategySignal]) -> BacktestRun:
    ids = iter(f"c{i}" for i in range(1000))
    return replay(
        bars,
        strategy=_ScriptedStrategy(script),
        kernel=kernel(),
        window_start=_WINDOW,
        window_end=_END,
        fees_at=_fees_at,
        correlation_id=lambda: next(ids),
    )


def _at(hours: int) -> datetime:
    return _WINDOW + timedelta(hours=hours)


def test_warm_up_signals_are_not_executed() -> None:
    """An entry signal closing before the window start is skipped: no fill, not counted."""
    before = _WINDOW - timedelta(hours=4)
    run = _run([_bar(before, Decimal("100"))], {before: _entry(before, Decimal("100"))})
    assert (run.signals_seen, run.fills, dict(run.declined)) == (0, [], {})


def test_entry_fills_five_bps_over_close_and_records_close_as_mid() -> None:
    """§6.2: entry fill = close * 1.0005 and the M-02 mid is the bar close."""
    t = _at(4)
    run = _run([_bar(t, Decimal("200"))], {t: _entry(t, Decimal("200"))})
    assert run.signals_seen == 1
    assert run.fills[0].avg_price == Decimal("200") * (1 + Decimal("5") / _BPS)
    assert run.reference_prices == [Decimal("200")]
    assert run.trades[0].occurred_at == t


def test_chandelier_exit_fills_fifteen_bps_under_the_stop() -> None:
    """§6.2: a stop exit fills at stop * 0.9985 and the M-02 mid is the stop."""
    t1, t2 = _at(4), _at(8)
    run = _run(
        [_bar(t1, Decimal("200")), _bar(t2, Decimal("190"))],
        {t1: _entry(t1, Decimal("200")), t2: _exit(t2, Decimal("192"), "chandelier")},
    )
    expected = Decimal("192") * (1 - Decimal("15") / _BPS)
    assert abs(run.fills[1].avg_price - expected) < _PRECISION
    assert run.reference_prices == [Decimal("200"), Decimal("192")]


def test_time_stop_exit_fills_five_bps_under_close() -> None:
    """A non-stop exit is a market order: close * 0.9995, mid = close."""
    t1, t2 = _at(4), _at(8)
    run = _run(
        [_bar(t1, Decimal("200")), _bar(t2, Decimal("210"))],
        {t1: _entry(t1, Decimal("200")), t2: _exit(t2, Decimal("150"), "time_stop")},
    )
    expected = Decimal("210") * (1 - Decimal("5") / _BPS)
    assert abs(run.fills[1].avg_price - expected) < _PRECISION
    assert run.reference_prices[1] == Decimal("210")


def test_zero_volume_bar_declines_as_no_book() -> None:
    """With no traded volume there is no depth to fill against: declined, not filled."""
    t = _at(4)
    run = _run([_bar(t, Decimal("200"), volume=Decimal("0"))], {t: _entry(t, Decimal("200"))})
    assert (run.fills, dict(run.declined), run.rejections) == ([], {"no-book": 1}, [])


def test_order_larger_than_the_book_is_a_rejection() -> None:
    """A bar too thin for the order yields a fill-model rejection, the M-04 numerator."""
    t = _at(4)
    run = _run([_bar(t, Decimal("200"), volume=Decimal("0.0001"))], {t: _entry(t, Decimal("200"))})
    assert run.fills == []
    assert dict(run.declined) == {"fill_model": 1}
    assert len(run.rejections) == 1


def test_exit_without_position_is_counted_as_declined() -> None:
    """An exit whose entry never filled is declined as no-position (strategy/portfolio drift)."""
    t = _at(4)
    run = _run([_bar(t, Decimal("200"))], {t: _exit(t, Decimal("190"), "chandelier")})
    assert dict(run.declined) == {"no-position": 1}


def test_equity_curve_holds_marked_equity_at_in_window_daily_closes() -> None:
    """Only in-window 1D closes are recorded, each with equity marked at that close."""
    t1 = _at(4)
    d_before = _WINDOW - timedelta(days=1)
    d1 = _WINDOW + timedelta(days=1)
    bars = [
        _bar(d_before, Decimal("100"), interval=KlineInterval.D1),
        _bar(t1, Decimal("100")),
        _bar(d1, Decimal("110"), interval=KlineInterval.D1),
    ]
    run = _run(bars, {t1: _entry(t1, Decimal("100"))})
    assert [at for at, _ in run.daily_equity] == [d1]
    fill = run.fills[0]
    with localcontext(EXACT_CONTEXT):
        cash = Decimal("5000") - fill.executed_qty * fill.avg_price - fill.fee.amount
        expected = cash + fill.executed_qty * Decimal("110")
    assert run.daily_equity[0][1] == expected


def test_strategy_sees_the_portfolio_marked_at_the_bar_close() -> None:
    """The bar marks the position before on_bar, so the strategy sees current equity."""
    t1, t2 = _at(4), _at(8)
    strategy = _ScriptedStrategy({t1: _entry(t1, Decimal("100"))})
    k = kernel()
    replay(
        [_bar(t1, Decimal("100")), _bar(t2, Decimal("120"))],
        strategy=strategy,
        kernel=k,
        window_start=_WINDOW,
        window_end=_END,
        fees_at=_fees_at,
        correlation_id=lambda: "c",
    )
    assert strategy.seen[1].current_value == k.tracker.snapshot.equity
    assert strategy.seen[1].current_value > strategy.seen[0].current_value


def test_default_execution_model_is_the_pre_registered_one() -> None:
    """Defaults: 5 bps entries, 15 bps stops, 30% of bar volume per side."""
    assert ExecutionModel() == ExecutionModel(
        entry_bps=Decimal("5"), stop_bps=Decimal("15"), depth_frac=Decimal("0.30")
    )
