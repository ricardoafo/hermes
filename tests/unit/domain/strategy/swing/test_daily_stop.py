"""Tests for the daily-moved chandelier stop of SwingStrategyAdapter (#785).

Pre-registration v1 §5.3: the exit is an on-exchange stop-market at the
chandelier level, "posted immediately after entry fill; moved daily as
trailing stop advances". So the stop a 4h bar is tested against was set
before that bar opened: at the entry fill, or at the latest daily close since.
A 4h bar never raises the stop it is tested against, and the stop moves only
when a day closes, and only up (#680).

Hand-worked examples use period 5 and flat bars (close 100, high 101, low 99):
every true range is 2, so ATR(5) = 2 and the level is 101 - 3 * 2 = 95. The
4h bars start at a UTC midnight, so bar ``i`` ends a day when ``(i + 1) % 6 == 0``
(bars 5, 11, 17, 23, ...).

Reference: pre-registration-v1.md §5.2, §5.3; GitHub issues #680, #785.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.stop_mark import StopRecord
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_D1_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_DAILY_SEEDS = 12
_H4_BASE = _D1_BASE + timedelta(days=_DAILY_SEEDS)
_BARS_PER_DAY = 6
_FLAT_BARS = 12
_ENTRY_INDEX = 3
_STATE = PortfolioState(
    peak_value=Decimal("10000"),
    current_value=Decimal("10000"),
    realized_slippage_cumulative=Decimal("0"),
    correlations={},
    dd_halt_required=False,
)


def _h4(index: int, *, high: str = "101", low: str = "99") -> Bar:
    open_time = _H4_BASE + timedelta(hours=4 * index)
    return Bar(
        pair=_PAIR,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=4),
    )


def _ends_a_day(bar: Bar) -> bool:
    return (bar.open_time + timedelta(hours=4)).hour == 0


def _adapter() -> SwingStrategyAdapter:
    return SwingStrategyAdapter(
        donchian_period=5,
        adx_period=5,
        adx_threshold=Decimal("10"),
        ema_period=10,
        chandelier_period=5,
        chandelier_atr_mult=Decimal("3.0"),
        time_stop_days=90,
        atr_sizing_period=5,
    )


def _held(last_stop: str | None = None) -> SwingStrategyAdapter:
    """Return an adapter restored into a position over ``_FLAT_BARS`` flat bars (stop 95)."""
    adapter = _adapter()
    adapter.seed(
        [_h4(i) for i in range(_FLAT_BARS)],
        open_position=OpenPosition(
            entry_time=_h4(_ENTRY_INDEX).close_time,
            entry_price=Decimal("100"),
            last_stop=None if last_stop is None else Decimal(last_stop),
        ),
    )
    return adapter


def _feed(
    adapter: SwingStrategyAdapter, bars: list[Bar]
) -> tuple[StrategySignal | None, StopRecord]:
    """Feed ``bars``; return the last bar's signal and stop record."""
    signal: StrategySignal | None = None
    for bar in bars:
        signal = adapter.on_bar(bar, _STATE)
    record = adapter.last_stop_record
    assert record is not None
    return signal, record


class TestTheBarTestedNeverRaisesItsOwnStop:
    """A 4h bar is tested against the stop posted before it opened."""

    def test_a_wide_up_bar_does_not_stop_itself_out(self) -> None:
        """High 120, low 99 under a posted 95: no exit (its own level would be 102.6)."""
        adapter = _held()

        signal, record = _feed(adapter, [_h4(_FLAT_BARS, high="120", low="99")])

        assert (signal, record.stop_price) == (None, Decimal("95"))

    def test_an_up_bar_inside_the_day_leaves_the_stop_in_force(self) -> None:
        """High 104 on bar 15, flat bar 16: the stop after bar 16 is still the posted 95."""
        adapter = _held()
        bars = [_h4(12), _h4(13), _h4(14), _h4(15, high="104", low="99.5"), _h4(16)]

        _, record = _feed(adapter, bars)

        assert record.stop_price == Decimal("95")


class TestTheStopMovesAtTheDailyClose:
    """The level is posted when a day closes, from the 4h bars closed by then."""

    def test_the_bar_ending_the_day_posts_the_new_level(self) -> None:
        """Bar 17 ends the day: HH 104 - 3 * ATR 2.32 = 97.04 becomes the stop in force."""
        adapter = _held()
        bars = [_h4(12), _h4(13), _h4(14), _h4(15, high="104", low="99.5"), _h4(16), _h4(17)]

        _, record = _feed(adapter, bars)

        assert (record.stop_price, record.chandelier_level, record.stop_moved) == (
            Decimal("97.04"),
            Decimal("97.04"),
            True,
        )

    def test_the_posted_level_stops_the_next_day_out(self) -> None:
        """Bar 18 low 97 under the 97.04 posted at bar 17's close: the exit fires at 97.04."""
        adapter = _held()
        bars = [_h4(12), _h4(13), _h4(14), _h4(15, high="104", low="99.5"), _h4(16), _h4(17)]
        _feed(adapter, bars)

        signal, _ = _feed(adapter, [_h4(18, low="97")])

        assert signal is not None
        assert (signal.exit_reason, signal.stop_price) == ("chandelier", Decimal("97.04"))

    def test_a_lower_daily_level_never_lowers_the_stop(self) -> None:
        """A stored 97 over the flat daily level 95 at bar 17: the stop stays 97."""
        adapter = _held("97")

        _, record = _feed(adapter, [_h4(i) for i in range(12, 18)])

        assert (record.stop_price, record.chandelier_level) == (Decimal("97"), Decimal("95"))


def _ramp_bar(close: Decimal, index: int, interval: KlineInterval = KlineInterval.H4) -> Bar:
    hours = 4 if interval is KlineInterval.H4 else 24
    base = _H4_BASE if interval is KlineInterval.H4 else _D1_BASE
    open_time = base + timedelta(hours=hours * index)
    return Bar(
        pair=_PAIR,
        interval=interval,
        open_time=open_time,
        open=close,
        high=close + Decimal("2"),
        low=max(close - Decimal("2"), Decimal("0.01")),
        close=close,
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=hours),
    )


def _positions(steps: list[Decimal]) -> list[list[tuple[Bar, StopRecord]]]:
    """Run the real adapter over a ramp then ``steps``; group each position's records."""
    adapter = _adapter()
    for i in range(_DAILY_SEEDS):
        adapter.on_bar(_ramp_bar(Decimal("900") + Decimal("10") * i, i, KlineInterval.D1), _STATE)
    closes = [Decimal("1000") + Decimal("15") * i for i in range(30)]
    for step in steps:
        closes.append(max(closes[-1] + step, Decimal("10")))
    positions: list[list[tuple[Bar, StopRecord]]] = []
    holding = False
    for i, close in enumerate(closes):
        bar = _ramp_bar(close, i)
        signal = adapter.on_bar(bar, _STATE)
        record = adapter.last_stop_record
        if signal is not None and signal.kind is SignalKind.ENTRY:
            positions.append([])
            holding = True
        if holding and record is not None:
            positions[-1].append((bar, record))
        if signal is not None and signal.kind is SignalKind.EXIT:
            holding = False
    return positions


_steps = st.lists(
    st.decimals(min_value=Decimal("-60"), max_value=Decimal("40"), places=2),
    min_size=5,
    max_size=60,
)


class TestTheStopOnlyMovesWhenADayCloses:
    """Property: over any price path, the stop in force changes only on a bar ending a day."""

    @given(steps=_steps)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_a_stop_change_falls_on_a_daily_close(self, steps: list[Decimal]) -> None:
        """Each change of the recorded stop within a position is on a bar that ends a UTC day."""
        for position in _positions(steps):
            for (_, before), (bar, after) in pairwise(position):
                if after.stop_price != before.stop_price:
                    assert _ends_a_day(bar), f"stop moved on {bar.open_time:%H:%M} bar"
