"""Tests for the backtest replay loop and its §6.2 execution books.

Invariants (backtest-driver-mvp1-phase2 change.md D-1, D-4, R-2):

- Signals before the window warm the strategy up and are never executed; a
  warm-up entry returns the strategy to flat like a refused entry, so the
  window starts flat (#723).
- An entry fills at close * 1.0005; a chandelier exit at stop * 0.9985; a
  time-stop or regime-break exit at close * 0.9995; the reference price is
  recorded as the M-02 mid.
- A zero-volume bar has no book, so its signal is declined as ``no-book``.
- An entry the kernel does not fill returns the strategy to flat (#707): no
  later exit for a position that does not exist, and the pair can enter again.
- An exit the kernel does not fill returns the strategy to the position still
  held, with its entry time and price (#706): the next qualifying bar exits
  it, once. An exit for a pair not held leaves the strategy flat.
- The equity curve holds the marked equity at each in-window 1D close.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from hermes.application.backtest.replay import BacktestRun, ExecutionModel, replay
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.precision import EXACT_CONTEXT
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
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
    restored: list[OpenPosition | None] = field(default_factory=list)

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        self.seen.append(portfolio_state)
        if bar.interval is not KlineInterval.H4:
            return None
        return self.script.get(bar.close_time)

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        _ = (bars, open_position)

    def restore_position(self, open_position: OpenPosition | None) -> None:
        self.restored.append(open_position)


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


def _replay_with(
    strategy: _ScriptedStrategy | SwingStrategyAdapter,
    bars: list[Bar],
    *,
    window_start: datetime = _WINDOW,
) -> BacktestRun:
    ids = iter(f"c{i}" for i in range(1000))
    return replay(
        bars,
        strategy=strategy,
        kernel=kernel(),
        window_start=window_start,
        window_end=_END,
        fees_at=_fees_at,
        correlation_id=lambda: next(ids),
    )


def test_declined_entry_restores_the_strategy_to_flat() -> None:
    """A no-book entry is followed by exactly one restore to flat (#707)."""
    t = _at(4)
    strategy = _ScriptedStrategy({t: _entry(t, Decimal("200"))})
    run = _replay_with(strategy, [_bar(t, Decimal("200"), volume=Decimal("0"))])
    assert (dict(run.declined), strategy.restored) == ({"no-book": 1}, [None])


def test_declined_exit_restores_the_filled_entry() -> None:
    """A filled entry, then a no-book exit: one restore, to the entry's time and price (#706)."""
    t1, t2 = _at(4), _at(8)
    strategy = _ScriptedStrategy(
        {t1: _entry(t1, Decimal("200")), t2: _exit(t2, Decimal("190"), "chandelier")}
    )
    run = _replay_with(
        strategy,
        [_bar(t1, Decimal("200")), _bar(t2, Decimal("190"), volume=Decimal("0"))],
    )
    assert (len(run.fills), dict(run.declined)) == (1, {"no-book": 1})
    assert strategy.restored == [OpenPosition(entry_time=t1, entry_price=Decimal("200"))]


def test_declined_exit_for_a_pair_not_held_restores_flat() -> None:
    """An exit with no position behind it is declined and leaves the strategy flat."""
    t = _at(4)
    strategy = _ScriptedStrategy({t: _exit(t, Decimal("190"), "chandelier")})
    run = _replay_with(strategy, [_bar(t, Decimal("190"))])
    assert (dict(run.declined), strategy.restored) == ({"no-position": 1}, [None])


def test_entries_passed_in_carry_a_held_position_across_calls() -> None:
    """A caller replaying bar by bar keeps the entry, so a later declined exit restores it."""
    t1, t2 = _at(4), _at(8)
    strategy = _ScriptedStrategy(
        {t1: _entry(t1, Decimal("200")), t2: _exit(t2, Decimal("190"), "chandelier")}
    )
    k = kernel()
    entries: dict[Pair, OpenPosition] = {}
    for bar in (_bar(t1, Decimal("200")), _bar(t2, Decimal("190"), volume=Decimal("0"))):
        replay(
            [bar],
            strategy=strategy,
            kernel=k,
            window_start=_WINDOW,
            window_end=_END,
            fees_at=_fees_at,
            correlation_id=lambda: "c",
            entries=entries,
        )
    assert strategy.restored == [OpenPosition(entry_time=t1, entry_price=Decimal("200"))]


def test_warm_up_entry_restores_flat_once_and_warm_up_exit_restores_nothing() -> None:
    """A warm-up entry counts as refused: one restore to flat; a warm-up exit adds none (#723)."""
    t1, t2 = _WINDOW - timedelta(hours=8), _WINDOW - timedelta(hours=4)
    strategy = _ScriptedStrategy(
        {t1: _entry(t1, Decimal("200")), t2: _exit(t2, Decimal("190"), "chandelier")}
    )
    run = _replay_with(strategy, [_bar(t1, Decimal("200")), _bar(t2, Decimal("190"))])
    assert (run.signals_seen, strategy.restored) == (0, [None])


_SWING_H4_BASE = _WINDOW + timedelta(days=15)


def _swing_bar(close: str, index: int, *, daily: bool = False, volume: str = "1000") -> Bar:
    """Build a bar of the small-period swing script; 4h bars follow every 1d bar."""
    span = timedelta(days=1) if daily else timedelta(hours=4)
    open_time = (_WINDOW if daily else _SWING_H4_BASE) + span * index
    price = Decimal(close)
    return Bar(
        pair=BTC_USDT,
        interval=KlineInterval.D1 if daily else KlineInterval.H4,
        open_time=open_time,
        open=price,
        high=price + Decimal("2"),
        low=price - Decimal("2"),
        close=price,
        volume=Decimal(volume),
        close_time=open_time + span,
    )


def _swing_strategy() -> SwingStrategyAdapter:
    return SwingStrategyAdapter(
        donchian_period=5,
        adx_period=5,
        adx_threshold=Decimal("5"),
        ema_period=10,
        chandelier_period=5,
        chandelier_atr_mult=Decimal("3.0"),
        atr_sizing_period=5,
    )


def _refused_breakout_history() -> list[Bar]:
    """Rising 1d bars, eleven flat 4h bars, then a breakout with no volume (no book)."""
    daily = [_swing_bar(str(1000 + 200 * i // 11), i, daily=True) for i in range(12)]
    flat = [_swing_bar("1000", i) for i in range(11)]
    return [*daily, *flat, _swing_bar("2000", 11, volume="0")]


def test_real_strategy_after_a_refused_entry_signals_no_exit() -> None:
    """After a no-book entry, a crash bar raises no exit, so nothing is declined no-position."""
    crash = dataclasses.replace(_swing_bar("500", 12), low=Decimal("100"))
    run = _replay_with(_swing_strategy(), [*_refused_breakout_history(), crash])
    assert (run.signals_seen, dict(run.declined), run.fills) == (1, {"no-book": 1}, [])


def test_real_strategy_enters_again_after_a_refused_entry() -> None:
    """After a no-book entry, the next breakout is a new entry and fills at close + 5 bps."""
    run = _replay_with(_swing_strategy(), [*_refused_breakout_history(), _swing_bar("3000", 12)])
    assert (run.signals_seen, dict(run.declined)) == (2, {"no-book": 1})
    assert [(f.order.side.value, f.avg_price) for f in run.fills] == [
        ("BUY", Decimal("3000") * Decimal("1.0005"))
    ]


def _filled_breakout_history() -> list[Bar]:
    """Rising 1d bars, eleven flat 4h bars, then a breakout with volume (a filled entry)."""
    daily = [_swing_bar(str(1000 + 200 * i // 11), i, daily=True) for i in range(12)]
    flat = [_swing_bar("1000", i) for i in range(11)]
    return [*daily, *flat, _swing_bar("2000", 11)]


def _crash(index: int, *, volume: str = "1000") -> Bar:
    return dataclasses.replace(_swing_bar("500", index, volume=volume), low=Decimal("100"))


def test_real_strategy_exits_on_the_bar_after_a_declined_exit() -> None:
    """A no-book chandelier exit is retried on the next crash bar and fills: one SELL (#706)."""
    run = _replay_with(
        _swing_strategy(),
        [*_filled_breakout_history(), _crash(12, volume="0"), _crash(13)],
    )
    assert (run.signals_seen, dict(run.declined)) == (3, {"no-book": 1})
    assert [f.order.side.value for f in run.fills] == ["BUY", "SELL"]


def test_real_strategy_is_flat_after_the_retried_exit_fills() -> None:
    """After the retried exit fills, a further crash bar signals nothing: the strategy is flat."""
    run = _replay_with(
        _swing_strategy(),
        [*_filled_breakout_history(), _crash(12, volume="0"), _crash(13), _crash(14)],
    )
    assert (run.signals_seen, dict(run.declined), len(run.fills)) == (3, {"no-book": 1}, 2)


def test_real_strategy_starts_the_window_flat_after_a_warm_up_breakout() -> None:
    """A warm-up breakout leaves the strategy flat, so the window's own breakout fills (#723).

    Without the reset the adapter would still be long at window start, skip the
    window's breakout and later signal an exit for a position never held.
    """
    daily = [_swing_bar(str(1000 + 200 * i // 11), i, daily=True) for i in range(12)]
    flat = [_swing_bar("1000", i) for i in range(11)]
    warm_breakout = _swing_bar("2000", 11)
    window_breakout = _swing_bar("3000", 12)
    run = _replay_with(
        _swing_strategy(),
        [*daily, *flat, warm_breakout, window_breakout],
        window_start=window_breakout.close_time,
    )
    assert (run.signals_seen, dict(run.declined)) == (1, {})
    assert [(f.order.side.value, f.avg_price) for f in run.fills] == [
        ("BUY", Decimal("3000") * Decimal("1.0005"))
    ]


class TestPortfolioRun:
    """One strategy per pair through one kernel (#822)."""

    def test_bar_of_a_pair_without_a_strategy_is_refused(self) -> None:
        """A mapping that does not cover a bar's pair is a wiring error, not a silent skip."""
        eth = Pair(base=Currency.ETH, quote=Currency.USDT)
        with pytest.raises(ValueError, match="no strategy for BTC/USDT"):
            replay(
                [_bar(_at(4), Decimal("100"))],
                strategy={eth: _ScriptedStrategy({})},
                kernel=kernel(),
                window_start=_WINDOW,
                window_end=_END,
                fees_at=_fees_at,
                correlation_id=lambda: "c0",
            )

    def test_mapping_with_the_pair_runs_like_the_single_strategy(self) -> None:
        """{BTC: s} replays exactly as s alone: same fills and equity curve."""
        bars = [
            _bar(_at(4), Decimal("100")),
            _bar(_at(24), Decimal("101"), interval=KlineInterval.D1),
        ]
        script = {_at(4): _entry(_at(4), Decimal("100"))}
        single = _run(bars, script)
        ids = iter(f"c{i}" for i in range(1000))
        mapped = replay(
            bars,
            strategy={BTC_USDT: _ScriptedStrategy(script)},
            kernel=kernel(),
            window_start=_WINDOW,
            window_end=_END,
            fees_at=_fees_at,
            correlation_id=lambda: next(ids),
        )
        assert (mapped.fills, mapped.daily_equity) == (single.fills, single.daily_equity)
