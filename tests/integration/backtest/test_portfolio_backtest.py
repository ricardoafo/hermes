"""Integration test: a portfolio backtest shares one capital across three pairs (#822).

Real ``FillKernel`` (router, sizing, ``OrderbookWalkFillModel``), real
``PortfolioTracker``, real ``CorrelationLock`` and the real certify
performance reader; nothing is mocked. Bars are generated for BTC, ETH and
SOL from chosen daily returns, so the 30-day correlation between pairs is
known. Entries come from a scripted strategy where the test needs them at
fixed times, and from the real ``SwingStrategyAdapter`` for the round trips.

Invariants (822-portfolio-backtest change.md R-1..R-4):

- Two open pairs correlated above 0.80 block an entry in a third pair
  correlated above 0.80 with both; with the third pair uncorrelated, it fills.
- The §5.5 exposure cap binds across pairs: once two positions have grown,
  a third entry that would pass 15% of capital is refused.
- The equity curve has one point per day, not one per pair.
- The run's performance file is accepted by ``hermes certify`` and its trade
  count and drawdown are the run's own.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.files import LocalFiles
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.application.backtest.performance import performance_file
from hermes.application.backtest.replay import BacktestRun, replay
from hermes.application.calibration.certification_gates import GateEvidence
from hermes.application.execution.kernel import FillKernel
from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.strategy_protocol.strategy_factory import strategy_kind
from hermes.domain.calibration.trial_record import TrialSummary
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.statistics.performance import max_drawdown
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.certify import apply_performance
from hermes.ports.strategy import StrategyPort
from tests.support.fill_kernel import (
    BTC_USDT,
    SIZING,
    STRATEGY,
    fees_at,
    kill_evaluator,
    seed,
)
from tests.support.parity import ScriptedStrategy

pytestmark = pytest.mark.integration

_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
_PAIRS = (BTC_USDT, _ETH, _SOL)
_T0 = datetime(2023, 1, 1, tzinfo=UTC)

type DailyReturn = Callable[[int], Decimal]


def _wiggle(day: int) -> Decimal:
    """Return a non-constant daily move, so a return correlation is defined."""
    return Decimal((day * 7) % 5 - 2) / 100


def _opposite(day: int) -> Decimal:
    """Return the mirror of :func:`_wiggle`: correlation -1 with it."""
    return -_wiggle(day)


def _bars(returns: Mapping[Pair, DailyReturn], days: int) -> list[Bar]:
    """Six 4h bars per day plus the 1D bar per pair, in delivery order."""
    bars: list[Bar] = []
    for pair, move in returns.items():
        previous = Decimal("100")
        for day in range(days):
            close = previous * (1 + move(day))
            points = [previous + (close - previous) * Decimal(i) / 6 for i in range(7)]
            for i in range(6):
                lo, hi = sorted((points[i], points[i + 1]))
                opened = _T0 + timedelta(days=day, hours=4 * i)
                bars.append(
                    Bar(
                        pair=pair,
                        interval=KlineInterval.H4,
                        open_time=opened,
                        open=points[i],
                        high=hi * Decimal("1.001"),
                        low=lo * Decimal("0.999"),
                        close=points[i + 1],
                        volume=Decimal("1000"),
                        close_time=opened + timedelta(hours=4),
                    )
                )
            opened = _T0 + timedelta(days=day)
            bars.append(
                Bar(
                    pair=pair,
                    interval=KlineInterval.D1,
                    open_time=opened,
                    open=previous,
                    high=max(previous, close) * Decimal("1.001"),
                    low=min(previous, close) * Decimal("0.999"),
                    close=close,
                    volume=Decimal("6000"),
                    close_time=opened + timedelta(days=1),
                )
            )
            previous = close
    return sorted(bars, key=lambda b: (b.close_time, b.interval != KlineInterval.D1, str(b.pair)))


def _kernel() -> FillKernel:
    return FillKernel(
        fill_model=OrderbookWalkFillModel(),
        risk_engine=RiskEngine(rules=[CorrelationLock()]),
        kill_evaluator=kill_evaluator(),
        tracker=PortfolioTracker(seed()),
        strategy_id=STRATEGY,
        sizing=SIZING,
    )


def _run(
    bars: Sequence[Bar],
    strategies: Mapping[Pair, StrategyPort],
    *,
    window_start: datetime,
    window_end: datetime,
    kernel: FillKernel | None = None,
) -> BacktestRun:
    ids = iter(f"01HZ{i:022d}" for i in range(100_000))
    return replay(
        bars,
        strategy=strategies,
        kernel=kernel if kernel is not None else _kernel(),
        window_start=window_start,
        window_end=window_end,
        fees_at=fees_at,
        correlation_id=lambda: next(ids),
    )


def _entry_at(day: int) -> frozenset[datetime]:
    """Return the 4h close at 08:00 on ``day``."""
    return frozenset({_T0 + timedelta(days=day, hours=8)})


def _bought(run: BacktestRun) -> list[Pair]:
    return [f.order.pair for f in run.fills if f.order.side is OrderSide.BUY]


class TestCorrelationLock:
    """§5.5: the third correlated pair is blocked; an uncorrelated third pair is not."""

    # SEV-1: the signed correlation lock must refuse the third correlated entry (pre-reg §5.5)
    def test_third_correlated_entry_is_refused(self) -> None:
        """All three pairs move alike (correlation 1): BTC and ETH fill, SOL is refused."""
        bars = _bars(dict.fromkeys(_PAIRS, _wiggle), days=45)
        run = _run(
            bars,
            {p: ScriptedStrategy(at=_entry_at(40)) for p in _PAIRS},
            window_start=_T0 + timedelta(days=35),
            window_end=_T0 + timedelta(days=45),
        )
        assert (_bought(run), dict(run.declined)) == (
            [BTC_USDT, _ETH],
            {"correlation-lock": 1},
        )

    def test_uncorrelated_third_entry_fills(self) -> None:
        """SOL moves opposite to BTC and ETH: its entry passes the lock and fills.

        SOL enters a day after BTC and ETH, once their 2% drop has marked
        them under 250 each, so the exposure cap leaves room for it.
        """
        bars = _bars({BTC_USDT: _wiggle, _ETH: _wiggle, _SOL: _opposite}, days=45)
        run = _run(
            bars,
            {
                BTC_USDT: ScriptedStrategy(at=_entry_at(40)),
                _ETH: ScriptedStrategy(at=_entry_at(40)),
                _SOL: ScriptedStrategy(at=_entry_at(41)),
            },
            window_start=_T0 + timedelta(days=35),
            window_end=_T0 + timedelta(days=45),
        )
        assert (_bought(run), dict(run.declined)) == ([BTC_USDT, _ETH, _SOL], {})

    def test_too_little_history_leaves_the_lock_open(self) -> None:
        """Before 30 daily returns exist the coefficient is unknown, so the entry fills."""
        bars = _bars(dict.fromkeys(_PAIRS, _wiggle), days=25)
        run = _run(
            bars,
            {
                BTC_USDT: ScriptedStrategy(at=_entry_at(20)),
                _ETH: ScriptedStrategy(at=_entry_at(20)),
                _SOL: ScriptedStrategy(at=_entry_at(21)),
            },
            window_start=_T0 + timedelta(days=15),
            window_end=_T0 + timedelta(days=25),
        )
        assert _bought(run) == [BTC_USDT, _ETH, _SOL]


class TestSharedCapital:
    """The pairs share one capital and one exposure cap."""

    def test_third_entry_on_the_same_bar_is_refused_by_the_exposure_cap(self) -> None:
        """Three entries at one close: the first two fill 5 bps over the close (§6.2).

        They are marked at that fill price, 250.125 each, so the third 250 entry
        would make 750.25, over the 750 cap. The cap is checked at marked
        value (sizing-per-prereg), so simultaneous breakouts fill two pairs.
        """
        bars = _bars({BTC_USDT: _wiggle, _ETH: _wiggle, _SOL: _opposite}, days=45)
        run = _run(
            bars,
            {p: ScriptedStrategy(at=_entry_at(40)) for p in _PAIRS},
            window_start=_T0 + timedelta(days=35),
            window_end=_T0 + timedelta(days=45),
        )
        assert (_bought(run), dict(run.declined)) == ([BTC_USDT, _ETH], {"exposure-cap": 1})

    def test_exposure_cap_refuses_a_third_entry_after_positions_grew(self) -> None:
        """BTC and ETH (250 each) rise 2%/day; by day 50 a 250 SOL entry would pass 750."""

        def rise_after_entry(day: int) -> Decimal:
            return _wiggle(day) if day <= 40 else Decimal("0.02")

        bars = _bars({BTC_USDT: rise_after_entry, _ETH: rise_after_entry, _SOL: _opposite}, 55)
        run = _run(
            bars,
            {
                BTC_USDT: ScriptedStrategy(at=_entry_at(40)),
                _ETH: ScriptedStrategy(at=_entry_at(40)),
                _SOL: ScriptedStrategy(at=_entry_at(50)),
            },
            window_start=_T0 + timedelta(days=35),
            window_end=_T0 + timedelta(days=55),
        )
        assert (_bought(run), dict(run.declined)) == ([BTC_USDT, _ETH], {"exposure-cap": 1})

    def test_equity_curve_has_one_point_per_day(self) -> None:
        """Three pairs close daily at midnight; the curve records each midnight once."""
        bars = _bars(dict.fromkeys(_PAIRS, _wiggle), days=45)
        run = _run(
            bars,
            {p: ScriptedStrategy(at=frozenset()) for p in _PAIRS},
            window_start=_T0 + timedelta(days=35),
            window_end=_T0 + timedelta(days=45),
        )
        times = [t for t, _ in run.daily_equity]
        assert times == [_T0 + timedelta(days=d) for d in range(35, 46)]


def _swing_close(day: int) -> Decimal:
    """Flat at 100, then +1%/day for 30 days, then -4%/day (the fill-path shape)."""
    if day < 215:
        return Decimal("100")
    if day < 245:
        return Decimal("100") * Decimal("1.01") ** (day - 214)
    return Decimal("100") * Decimal("1.01") ** 30 * Decimal("0.96") ** (day - 244)


def _swing_move(day: int) -> Decimal:
    previous = _swing_close(day - 1) if day else Decimal("100")
    return _swing_close(day) / previous - 1


def test_performance_file_round_trips_through_certify(tmp_path: Path) -> None:
    """Swing round trips on one capital: certify reads the file with the run's numbers.

    All three pairs break out on the same bar, so the third entry meets the
    exposure cap; once 30 rising days make the pairs correlated, its retries
    meet the correlation lock. Two round trips close.
    """
    bars = _bars(dict.fromkeys(_PAIRS, _swing_move), days=260)
    run = _run(
        bars,
        {p: SwingStrategyAdapter() for p in _PAIRS},
        window_start=_T0 + timedelta(days=210),
        window_end=_T0 + timedelta(days=260),
    )
    payload = performance_file(
        run,
        trials=TrialSummary(count=1, validation_count=1, sharpe_variance=None),
        bootstrap_seed=0,
    )
    path = tmp_path / "train_val.json"
    path.write_text(json.dumps(payload))

    metrics = apply_performance(GateEvidence(), path, files=LocalFiles()).backtest

    assert metrics is not None
    assert (metrics.trade_count, sorted(run.declined), metrics.max_drawdown) == (
        2,
        ["correlation-lock", "exposure-cap"],
        pytest.approx(max_drawdown([float(e) for _, e in run.daily_equity])),
    )


# SEV-1: v1 and v2 must trade their signed rule after the factory lands (contract §2a)
def test_an_entry_with_no_signal_replays_exactly_as_the_swing_adapter() -> None:
    """The factory's default kind is today's adapter, byte for byte (issue #1074).

    Same bars, same kernel set-up: building through ``strategy_kind(None)``
    gives the same signals, fills, refusals and equity curve as building
    ``SwingStrategyAdapter()`` directly, as serve, parity and the driver did
    before the factory existed.
    """
    bars = _bars(dict.fromkeys(_PAIRS, _swing_move), days=260)
    window = {"window_start": _T0 + timedelta(days=210), "window_end": _T0 + timedelta(days=260)}
    kind = strategy_kind(None)

    direct = _run(bars, {p: SwingStrategyAdapter() for p in _PAIRS}, **window)
    built = _run(bars, {p: kind.create(kind.default_parameters()) for p in _PAIRS}, **window)

    assert (built.signals_seen, built.fills, built.declined, built.daily_equity) == (
        direct.signals_seen,
        direct.fills,
        direct.declined,
        direct.daily_equity,
    )
