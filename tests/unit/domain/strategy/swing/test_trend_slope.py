"""Tests for TrendSlopeAdapter, research batch swing-b1 candidate C3 (#1065).

C3 is v2's rule plus one entry condition: the daily EMA(200) at the last daily
close is above its value ``ema_slope_lookback`` daily closes earlier. These
tests pin that the extra condition is the only difference from v2.

Reference: docs/experiments/swing-mvp1/cards/swing-b1-trend-slope.md;
research batch swing-b1 §5 (C3).
"""

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.application.strategy_protocol.strategy_factory import DONCHIAN_SLOPE, strategy_kind
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter, swing_parameters
from hermes.domain.strategy.swing.trend_slope import SlopeEntryCheck, TrendSlopeAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config import StrategiesConfig
from hermes.infrastructure.config.loader import load_yaml_model
from tests.support.paths import REPO_ROOT

_REPO_CONFIG = REPO_ROOT / "config"
_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_D1_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_DAILY_BARS = 21
_H4_BASE = _D1_BASE + timedelta(days=_DAILY_BARS)

_EMA = 10
_LOOKBACK = 3

_FALLING_THEN_RALLY = [Decimal(300 - 10 * i) for i in range(_DAILY_BARS - 1)] + [Decimal(260)]
"""The last close is above EMA(10), but the EMA is lower than three closes earlier."""

_RISING = [Decimal(100 + 10 * i) for i in range(_DAILY_BARS)]


def _bar(close: Decimal, index: int, interval: KlineInterval) -> Bar:
    hours = 4 if interval == KlineInterval.H4 else 24
    base = _H4_BASE if interval == KlineInterval.H4 else _D1_BASE
    open_time = base + timedelta(hours=hours * index)
    return Bar(
        pair=_BTC_USDT,
        interval=interval,
        open_time=open_time,
        open=close,
        high=close + Decimal("2"),
        low=max(close - Decimal("2"), Decimal("0.01")),
        close=close,
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=hours),
    )


def _state() -> PortfolioState:
    return PortfolioState(
        peak_value=Decimal("10000"),
        current_value=Decimal("10000"),
        realized_slippage_cumulative=Decimal("0"),
        correlations={},
        dd_halt_required=False,
    )


def _stream(daily: list[Decimal], h4: list[Decimal]) -> list[Bar]:
    """Daily bars first (all closed before the first 4h bar), then the 4h bars."""
    return [_bar(c, i, KlineInterval.D1) for i, c in enumerate(daily)] + [
        _bar(c, i, KlineInterval.H4) for i, c in enumerate(h4)
    ]


def _signals(adapter: SwingStrategyAdapter, bars: list[Bar]) -> list[StrategySignal | None]:
    state = _state()
    return [adapter.on_bar(bar, state) for bar in bars]


def _v2() -> SwingStrategyAdapter:
    """v2 with short lookbacks, so a few dozen bars exercise every condition."""
    return SwingStrategyAdapter(
        donchian_period=5,
        adx_period=5,
        adx_threshold=Decimal("10"),
        ema_period=_EMA,
        chandelier_period=5,
        time_stop_days=90,
        atr_sizing_period=5,
    )


def _c3() -> TrendSlopeAdapter:
    """C3 with the same short lookbacks as :func:`_v2`."""
    return TrendSlopeAdapter(
        ema_slope_lookback=_LOOKBACK,
        donchian_period=5,
        adx_period=5,
        adx_threshold=Decimal("10"),
        ema_period=_EMA,
        chandelier_period=5,
        time_stop_days=90,
        atr_sizing_period=5,
    )


def _breakout_4h() -> list[Decimal]:
    return [Decimal(1000 + 10 * i) for i in range(30)]


def test_a_falling_daily_trend_blocks_the_entry_v2_takes() -> None:
    """With the daily close above a falling EMA, v2 enters and C3 stays flat."""
    bars = _stream(_FALLING_THEN_RALLY, _breakout_4h())
    v2 = _v2()
    c3 = _c3()

    v2_entries = [s for s in _signals(v2, bars) if s is not None and s.kind == SignalKind.ENTRY]
    c3_signals = [s for s in _signals(c3, bars) if s is not None]

    assert (len(v2_entries), c3_signals) == (1, [])


def test_the_blocked_entry_names_only_the_trend_condition() -> None:
    """The record shows v2's three conditions held and only ``rising`` failed."""
    c3 = _c3()
    v2 = _v2()
    state = _state()
    for bar in _stream(_FALLING_THEN_RALLY, _breakout_4h()):
        c3.on_bar(bar, state)
        if v2.on_bar(bar, state) is not None:
            break

    assert c3.last_entry_check == SlopeEntryCheck(
        breakout=True, momentum=True, regime=True, rising=False
    )


def test_a_rising_daily_trend_lets_v2s_entry_through_unchanged() -> None:
    """With a rising daily EMA, C3 emits exactly v2's entry signal on the same bar."""
    bars = _stream(_RISING, _breakout_4h())

    v2 = _signals(_v2(), bars)
    c3 = _signals(_c3(), bars)

    assert any(s is not None for s in c3)
    assert c3 == v2


def test_too_little_daily_history_for_the_earlier_value_holds_rising_false() -> None:
    """Fewer than ema_period + lookback daily closes: no earlier EMA, so no entry."""
    too_few = _RISING[: _EMA + _LOOKBACK - 1]
    c3 = _c3()
    state = _state()
    checks = []
    for bar in _stream(too_few, _breakout_4h()):
        c3.on_bar(bar, state)
        if c3.last_entry_check is not None:
            checks.append(c3.last_entry_check)

    assert {getattr(check, "rising", None) for check in checks} == {False}


@pytest.mark.parametrize("lookback", [0, -1])
def test_a_slope_lookback_below_one_is_refused(lookback: int) -> None:
    """A lookback of zero would compare the EMA with itself; it is refused."""
    with pytest.raises(ValueError, match="ema_slope_lookback"):
        TrendSlopeAdapter(ema_slope_lookback=lookback)


def test_defaults_are_the_card_base_and_v2s_fixed_values() -> None:
    """The constructor defaults are C3's base values and v2's other §5 parameters."""
    defaults = {
        name: str(param.default)
        for name, param in inspect.signature(TrendSlopeAdapter.__init__).parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    }

    assert defaults == {**swing_parameters(), "ema_slope_lookback": "20"}


@st.composite
def _h4_closes(draw: st.DrawFn) -> list[Decimal]:
    """Draw a 4h close path that moves up and down, so entries and exits both occur."""
    steps = draw(st.lists(st.integers(min_value=-40, max_value=40), min_size=30, max_size=80))
    closes = [Decimal(5000)]
    for step in steps:
        closes.append(max(closes[-1] + Decimal(step), Decimal(100)))
    return closes


@given(h4=_h4_closes(), step=st.integers(min_value=1, max_value=50))
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_with_a_rising_daily_trend_c3_trades_exactly_like_v2(h4: list[Decimal], step: int) -> None:
    """Property: strictly rising daily closes make the filter a no-op, entries and exits alike."""
    daily = [Decimal(1000 + step * i) for i in range(_DAILY_BARS)]
    bars = _stream(daily, h4)

    v2 = _signals(_v2(), bars)
    c3 = _signals(_c3(), bars)

    assert c3 == v2


@given(
    h4=_h4_closes(),
    daily=st.lists(st.integers(min_value=100, max_value=400), min_size=14, max_size=_DAILY_BARS),
)
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_c3_never_enters_where_v2_would_not(h4: list[Decimal], daily: list[int]) -> None:
    """Property: from flat, every C3 entry is on a bar where v2's three conditions also held."""
    c3 = _c3()
    state = _state()
    for bar in _stream([Decimal(c) for c in daily], h4):
        signal = c3.on_bar(bar, state)
        if signal is not None and signal.kind == SignalKind.ENTRY:
            check = c3.last_entry_check
            assert check is not None
            assert (check.breakout, check.momentum, check.regime, check.all_met) == (
                True,
                True,
                True,
                True,
            )


def test_the_donchian_slope_kind_builds_c3_with_the_batch_parameter_names() -> None:
    """``signal: donchian_slope`` builds C3, and the batch's three parameters are its own."""
    kind = strategy_kind(DONCHIAN_SLOPE)
    defaults = kind.default_parameters()

    assert type(kind.create()) is TrendSlopeAdapter
    assert {
        name: defaults[name]
        for name in ("ema_slope_lookback", "donchian_period", "chandelier_atr_mult")
    } == {"ema_slope_lookback": 20, "donchian_period": 20, "chandelier_atr_mult": Decimal("3.0")}


def test_the_shipped_c3_entry_is_disabled_and_trades_donchian_slope() -> None:
    """The ``swing-b1-trend-slope`` roster entry names the C3 kind and never runs in serve."""
    roster = load_yaml_model(
        _REPO_CONFIG / "strategies.yaml", StrategiesConfig, env_section="STRATEGIES", env={}
    )
    entry = next(s for s in roster.strategies if s.name == "swing-b1-trend-slope")

    assert (entry.enabled, entry.strategy_kind().signal) == (False, DONCHIAN_SLOPE)
