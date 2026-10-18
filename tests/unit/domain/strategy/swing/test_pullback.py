"""Tests for PullbackAdapter, research batch swing-b1 candidate C4 (#1066).

Each test pins one rule of batch section C4 §5: the trend condition, the RSI
re-entry cross, the protective stop, and each of the four exits.

Reference: docs/experiments/swing-mvp1/cards/swing-b1-pullback.md;
research batch swing-b1 §5 (C4).
"""

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.application.strategy_protocol.strategy_factory import RSI_PULLBACK, strategy_kind
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.strategy.swing.pullback import PullbackAdapter, PullbackEntryCheck
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config import StrategiesConfig
from hermes.infrastructure.config.loader import load_yaml_model

_REPO_CONFIG = Path(__file__).resolve().parents[5] / "config"
_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_D1_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_DAILY_BARS = 21
_H4_BASE = _D1_BASE + timedelta(days=_DAILY_BARS)

_RISING_DAILY = [100 + 10 * i for i in range(_DAILY_BARS)]
_FALLING_DAILY = [400 - 10 * i for i in range(_DAILY_BARS)]

_DIP_AND_CROSS = [100 + i for i in range(12)] + [108, 104, 100, 103]
"""A rise, a three-bar pullback (RSI(3) down to 10), then RSI back above 35 on the last bar."""
_ENTRY_INDEX = len(_DIP_AND_CROSS) - 1
_HOVER = [102, 103] * 4
"""After the entry: RSI(3) between 35 and 70, lows far above the stop."""


def _adapter(*, time_stop_days: int = 20) -> PullbackAdapter:
    """C4 with short lookbacks, so a few dozen bars exercise every rule."""
    return PullbackAdapter(
        time_stop_days=time_stop_days,
        rsi_period=3,
        stop_atr_period=3,
        ema_period=10,
        trend_lookback=3,
        atr_sizing_period=3,
    )


def _bar(close: int, index: int, interval: KlineInterval) -> Bar:
    hours = 4 if interval == KlineInterval.H4 else 24
    base = _H4_BASE if interval == KlineInterval.H4 else _D1_BASE
    open_time = base + timedelta(hours=hours * index)
    price = Decimal(close)
    return Bar(
        pair=_BTC_USDT,
        interval=interval,
        open_time=open_time,
        open=price,
        high=price + Decimal("2"),
        low=max(price - Decimal("2"), Decimal("0.01")),
        close=price,
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=hours),
    )


def _daily(closes: list[int]) -> list[Bar]:
    return [_bar(c, i, KlineInterval.D1) for i, c in enumerate(closes)]


def _h4(closes: list[int]) -> list[Bar]:
    return [_bar(c, i, KlineInterval.H4) for i, c in enumerate(closes)]


def _state() -> PortfolioState:
    return PortfolioState(
        peak_value=Decimal("10000"),
        current_value=Decimal("10000"),
        realized_slippage_cumulative=Decimal("0"),
        correlations={},
        dd_halt_required=False,
    )


def _run(adapter: PullbackAdapter, bars: list[Bar]) -> list[StrategySignal | None]:
    state = _state()
    return [adapter.on_bar(bar, state) for bar in bars]


def _fired(signals: list[StrategySignal | None]) -> list[tuple[int, SignalKind, str | None]]:
    """(position in the stream, kind, exit reason) of every signal."""
    return [(i, s.kind, s.exit_reason) for i, s in enumerate(signals) if s is not None]


_ENTRY_AT = _DAILY_BARS + _ENTRY_INDEX


def test_an_rsi_cross_in_an_uptrend_enters_at_the_close() -> None:
    """RSI back above 35 while the daily trend is up: a long entry at the signal close."""
    signals = _run(_adapter(), _daily(_RISING_DAILY) + _h4(_DIP_AND_CROSS))

    entry = signals[_ENTRY_AT]
    assert _fired(signals) == [(_ENTRY_AT, SignalKind.ENTRY, None)]
    assert entry is not None
    assert entry.entry_price == Decimal(_DIP_AND_CROSS[-1])


def test_the_stop_is_the_signal_close_minus_m_atrs() -> None:
    """The protective stop is the signal close minus stop_atr_mult x ATR(stop period)."""
    h4 = _h4(_DIP_AND_CROSS)
    signals = _run(_adapter(), _daily(_RISING_DAILY) + h4)
    atr = AtrEngine.value(h4, period=3)
    assert atr is not None

    entry = signals[_ENTRY_AT]
    assert entry is not None
    assert entry.stop_price == h4[-1].close - Decimal("2.0") * atr


def test_no_entry_when_the_daily_trend_is_down() -> None:
    """The same cross with falling daily closes: no entry, and the record says why."""
    adapter = _adapter()
    signals = _run(adapter, _daily(_FALLING_DAILY) + _h4(_DIP_AND_CROSS))

    assert (_fired(signals), adapter.last_entry_check) == (
        [],
        PullbackEntryCheck(trend=False, reentry=True),
    )


def test_no_entry_when_rsi_never_closed_below_the_level() -> None:
    """A steady rise keeps RSI at 100: no cross from below, no entry."""
    signals = _run(_adapter(), _daily(_RISING_DAILY) + _h4([100 + i for i in range(20)]))

    assert _fired(signals) == []


def test_rsi_above_seventy_exits() -> None:
    """After the entry, RSI(3) closing above 70 exits with reason rsi_exit."""
    signals = _run(_adapter(), _daily(_RISING_DAILY) + _h4([*_DIP_AND_CROSS, 106, 109, 112]))

    assert _fired(signals)[-1] == (_ENTRY_AT + 3, SignalKind.EXIT, "rsi_exit")


def test_a_low_at_the_stop_exits() -> None:
    """A 4h low at or below the fixed stop exits with reason stop."""
    signals = _run(_adapter(), _daily(_RISING_DAILY) + _h4([*_DIP_AND_CROSS, 90]))

    assert _fired(signals)[-1] == (_ENTRY_AT + 1, SignalKind.EXIT, "stop")


def test_the_time_stop_fires_on_the_first_bar_at_or_after_the_deadline() -> None:
    """With a 1-day time stop, the sixth 4h bar after the entry closes at the deadline."""
    signals = _run(
        _adapter(time_stop_days=1), _daily(_RISING_DAILY) + _h4([*_DIP_AND_CROSS, *_HOVER])
    )

    assert _fired(signals)[-1] == (_ENTRY_AT + 6, SignalKind.EXIT, "time_stop")


def test_a_daily_close_below_the_ema_exits_on_the_next_4h_bar() -> None:
    """A new daily close below the daily EMA exits on the 4h bar closing at that daily close."""
    h4 = _h4([*_DIP_AND_CROSS, *_HOVER])
    crash = _bar(50, _DAILY_BARS + 2, KlineInterval.D1)
    stream = _daily(_RISING_DAILY) + h4[: _ENTRY_INDEX + 2] + [crash] + h4[_ENTRY_INDEX + 2 :]

    signals = _run(_adapter(), stream)

    assert crash.close_time == stream[_ENTRY_AT + 3].close_time
    assert _fired(signals)[-1] == (_ENTRY_AT + 3, SignalKind.EXIT, "regime_break")


def test_the_entry_bar_is_never_tested_for_an_exit() -> None:
    """A signal bar whose RSI is already above 70 still only enters."""
    signals = _run(_adapter(), _daily(_RISING_DAILY) + _h4([*_DIP_AND_CROSS[:-1], 130]))

    assert _fired(signals) == [(_ENTRY_AT, SignalKind.ENTRY, None)]


def test_a_restart_restores_the_entry_stop_from_the_buffered_entry_bar() -> None:
    """Seeded after the entry with no stored stop, the adapter exits on the same bar."""
    bars = _daily(_RISING_DAILY) + _h4([*_DIP_AND_CROSS, 104, 90])
    uninterrupted = _run(_adapter(), bars)
    entry = uninterrupted[_ENTRY_AT]
    assert entry is not None

    restarted = _adapter()
    restarted.seed(
        bars[: _ENTRY_AT + 2],
        OpenPosition(
            entry_time=entry.signal_time,
            entry_price=Decimal("103.05"),
            last_stop=None,
        ),
    )
    exit_signal = restarted.on_bar(bars[-1], _state())
    expected = uninterrupted[-1]

    assert exit_signal is not None
    assert expected is not None
    assert (exit_signal.exit_reason, exit_signal.stop_price, exit_signal.signal_time) == (
        expected.exit_reason,
        expected.stop_price,
        expected.signal_time,
    )


def test_a_restart_without_the_entry_bar_uses_the_stored_stop() -> None:
    """When the entry bar has left the buffer, the stop comes from the ledger's last_stop."""
    adapter = _adapter()
    adapter.seed(
        _daily(_RISING_DAILY) + _h4([104, 103, 104, 103, 104, 103]),
        OpenPosition(
            entry_time=_H4_BASE - timedelta(hours=4),
            entry_price=Decimal("103"),
            last_stop=Decimal("101.5"),
        ),
    )

    signal = adapter.on_bar(_bar(103, 6, KlineInterval.H4), _state())

    assert signal is not None
    assert (signal.exit_reason, signal.stop_price) == ("stop", Decimal("101.5"))


def test_seed_never_signals_and_restore_none_is_flat() -> None:
    """Seeding over a cross opens nothing: the next bar is an entry check, not an exit."""
    adapter = _adapter()
    adapter.seed(_daily(_RISING_DAILY) + _h4(_DIP_AND_CROSS), None)

    signal = adapter.on_bar(_bar(112, len(_DIP_AND_CROSS), KlineInterval.H4), _state())

    assert (signal, adapter.last_entry_check) == (
        None,
        PullbackEntryCheck(trend=True, reentry=False),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"time_stop_days": 0},
        {"rsi_period": 0},
        {"stop_atr_period": 0},
        {"trend_lookback": 0},
        {"stop_atr_mult": Decimal("0")},
        {"rsi_reentry": 70},
        {"rsi_reentry": 0},
    ],
)
def test_invalid_parameters_are_refused(overrides: dict[str, object]) -> None:
    """A non-positive period, stop or time stop, or a re-entry not below the exit, is refused."""
    with pytest.raises(ValueError, match=r"must be|need"):
        strategy_kind(RSI_PULLBACK).create(overrides)


def test_defaults_are_the_card_values() -> None:
    """The free parameters default to C4's base values, the fixed ones to the card's."""
    defaults = {
        name: str(param.default)
        for name, param in inspect.signature(PullbackAdapter.__init__).parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    }

    assert defaults == {
        "rsi_reentry": "35",
        "stop_atr_mult": "2.0",
        "time_stop_days": "20",
        "rsi_period": "14",
        "rsi_exit": "70",
        "stop_atr_period": "22",
        "ema_period": "200",
        "trend_lookback": "20",
        "atr_sizing_period": "14",
    }


@st.composite
def _path(draw: st.DrawFn) -> list[int]:
    """Draw a 4h close path with pullbacks and rallies, so entries and exits occur."""
    steps = draw(st.lists(st.integers(min_value=-6, max_value=6), min_size=20, max_size=90))
    closes = [500]
    for step in steps:
        closes.append(max(closes[-1] + step, 50))
    return closes


@given(path=_path())
@settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_signals_alternate_and_every_entry_meets_the_rule(path: list[int]) -> None:
    """Property: entries and exits alternate; each entry has both conditions and stop < entry."""
    adapter = _adapter(time_stop_days=2)
    state = _state()
    expected = SignalKind.ENTRY
    for bar in _daily(_RISING_DAILY) + _h4(path):
        signal = adapter.on_bar(bar, state)
        if signal is None:
            continue
        assert signal.kind == expected
        if signal.kind == SignalKind.ENTRY:
            assert adapter.last_entry_check == PullbackEntryCheck(trend=True, reentry=True)
            assert signal.entry_price is not None
            assert Decimal(0) < signal.stop_price < signal.entry_price
        expected = SignalKind.EXIT if expected == SignalKind.ENTRY else SignalKind.ENTRY


@given(path=_path(), cut=st.integers(min_value=0, max_value=89))
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_no_signal_depends_on_a_later_bar(path: list[int], cut: int) -> None:
    """Property: the signals up to bar T are the same whether or not later bars follow."""
    bars = _daily(_RISING_DAILY) + _h4(path)
    prefix = bars[: _DAILY_BARS + min(cut, len(path))]

    assert _run(_adapter(), prefix) == _run(_adapter(), bars)[: len(prefix)]


@given(path=_path())
@settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_a_falling_daily_trend_never_enters(path: list[int]) -> None:
    """Property: with falling daily closes the trend condition never holds, so no signal at all."""
    assert _fired(_run(_adapter(), _daily(_FALLING_DAILY) + _h4(path))) == []


def test_the_rsi_pullback_kind_builds_c4_with_the_batch_parameter_names() -> None:
    """``signal: rsi_pullback`` builds C4, and the batch's three parameters are its own."""
    kind = strategy_kind(RSI_PULLBACK)
    defaults = kind.default_parameters()

    assert type(kind.create()) is PullbackAdapter
    assert {
        name: defaults[name] for name in ("rsi_reentry", "stop_atr_mult", "time_stop_days")
    } == {
        "rsi_reentry": 35,
        "stop_atr_mult": Decimal("2.0"),
        "time_stop_days": 20,
    }


def test_the_shipped_c4_entry_is_disabled_and_trades_rsi_pullback() -> None:
    """The ``swing-b1-pullback`` roster entry names the C4 kind and never runs in serve."""
    roster = load_yaml_model(
        _REPO_CONFIG / "strategies.yaml", StrategiesConfig, env_section="STRATEGIES", env={}
    )
    entry = next(s for s in roster.strategies if s.name == "swing-b1-pullback")

    assert (entry.enabled, entry.strategy_kind().signal) == (False, RSI_PULLBACK)
