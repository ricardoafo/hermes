"""Tests for the chandelier stop ratchet of SwingStrategyAdapter (#680).

Pre-registration v1 §5.3 moves the exit stop "as trailing stop advances", and
its source SA-7 says the long chandelier stop "only moves up, never down". The
stop in force while a position is open is therefore the highest chandelier
level since entry: ``max(previous stop in force, HH - 3 * ATR)``. A new
position starts from its own raw level, and a restart continues from the
stored ``last_stop`` of the same position. Levels are posted at the entry and
at each daily close (§5.3, #785); bar ``i`` ends a UTC day when
``(i + 1) % 6 == 0``.

Hand-worked examples use period 5 and flat bars (close 100, high 101, low 99):
every true range is 2, so ATR(5) = 2 and the raw level is 101 - 3 * 2 = 95.
One wider bar of true range ``t`` after ten flat bars gives
ATR = (2 * 4 + t) / 5 (Wilder seed 2, then one smoothing step per TR).

Reference: pre-registration-v1.md §5.2, §5.3; sa7-strategy-design.md :153,
:261; integrity-checker-rules.md §4.1; GitHub issue #680.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
_FLAT_BARS = 12
_ENTRY_INDEX = 3
# Bar 17 is the last 4h bar of the third day: its close posts that day's level (#785).
_DAY_END = 17
_STATE = PortfolioState(
    peak_value=Decimal("10000"),
    current_value=Decimal("10000"),
    realized_slippage_cumulative=Decimal("0"),
    correlations={},
    dd_halt_required=False,
)


def _h4(index: int, *, close: str = "100", high: str = "101", low: str = "99") -> Bar:
    open_time = _H4_BASE + timedelta(hours=4 * index)
    return Bar(
        pair=_PAIR,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=4),
    )


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


def _held(last_stop: str | None) -> SwingStrategyAdapter:
    """Return an adapter restored into a position over ``_FLAT_BARS`` flat bars."""
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


def _record(adapter: SwingStrategyAdapter, bar: Bar) -> tuple[StrategySignal | None, StopRecord]:
    signal = adapter.on_bar(bar, _STATE)
    record = adapter.last_stop_record
    assert record is not None
    return signal, record


class TestStopHoldsWhenTheLevelFalls:
    """While a position is open the stop in force is the highest level since entry."""

    def test_a_falling_level_leaves_the_stop_where_it_was(self) -> None:
        """Level 95, then 93.2 posted at bar 17's daily close (TR 5, ATR 2.6): the stop stays 95."""
        adapter = _held(None)
        for index in range(_FLAT_BARS, _DAY_END):
            _record(adapter, _h4(index))

        signal, record = _record(adapter, _h4(_DAY_END, low="96"))

        assert signal is None
        assert (record.stop_price, record.chandelier_level, record.stop_moved) == (
            Decimal("95"),
            Decimal("93.2"),
            False,
        )

    def test_the_exit_fires_on_the_held_stop_the_raw_level_would_miss(self) -> None:
        """Low 94.5 the day after level 93.2 is posted: above that level, under the held 95."""
        adapter = _held(None)
        for index in range(_FLAT_BARS, _DAY_END):
            _record(adapter, _h4(index))
        _record(adapter, _h4(_DAY_END, low="96"))

        signal, record = _record(adapter, _h4(_DAY_END + 1, low="94.5"))

        assert signal is not None
        assert (signal.kind, signal.exit_reason, signal.stop_price) == (
            SignalKind.EXIT,
            "chandelier",
            Decimal("95"),
        )
        assert (record.stop_price, record.chandelier_level) == (Decimal("95"), Decimal("93.2"))

    def test_a_rising_level_raises_the_stop(self) -> None:
        """A stored 93 under the raw 95: the stop rises to 95 and is marked as moved."""
        adapter = _held("93")

        _, record = _record(adapter, _h4(_FLAT_BARS))

        assert (record.stop_price, record.chandelier_level, record.stop_moved) == (
            Decimal("95"),
            Decimal("95"),
            True,
        )


class TestRestartSeedsTheStop:
    """After a restart the stored ``last_stop`` of the same position is the floor."""

    def test_the_stored_stop_is_kept_over_a_lower_level(self) -> None:
        """Stored 97 over the raw 95: the first bar after the restart keeps 97."""
        adapter = _held("97")

        _, record = _record(adapter, _h4(_FLAT_BARS))

        assert (record.stop_price, record.chandelier_level, record.stop_moved) == (
            Decimal("97"),
            Decimal("95"),
            False,
        )

    def test_the_stored_stop_can_fire_the_exit(self) -> None:
        """Stored 97 and a low of 96.9: the exit fires at 97, the posted level 95 would not.

        The bar's own level (93.74) is never what it is tested against (#785).
        """
        adapter = _held("97")

        signal, record = _record(adapter, _h4(_FLAT_BARS, low="96.9"))

        assert signal is not None
        assert (signal.exit_reason, signal.stop_price) == ("chandelier", Decimal("97"))
        assert record.chandelier_level == Decimal("95")

    def test_no_stored_stop_starts_from_the_raw_level(self) -> None:
        """A restart without a stored stop takes the raw 95 and reports no move."""
        adapter = _held(None)

        _, record = _record(adapter, _h4(_FLAT_BARS))

        assert (record.stop_price, record.stop_moved) == (Decimal("95"), None)


def _ramp_daily(adapter: SwingStrategyAdapter) -> SwingStrategyAdapter:
    for i in range(_DAILY_SEEDS):
        adapter.on_bar(_ramp_bar(Decimal("900") + Decimal("10") * i, i, KlineInterval.D1), _STATE)
    return adapter


def _path(steps: list[Decimal]) -> list[Bar]:
    closes = [Decimal("1000") + Decimal("15") * i for i in range(30)]
    for step in steps:
        closes.append(max(closes[-1] + step, Decimal("10")))
    return [_ramp_bar(close, i) for i, close in enumerate(closes)]


def _positions(bars: list[Bar]) -> list[list[tuple[StrategySignal | None, StopRecord]]]:
    """Run the real adapter and group the stop records of each position, entry to exit."""
    adapter = _ramp_daily(_adapter())
    positions: list[list[tuple[StrategySignal | None, StopRecord]]] = []
    holding = False
    for bar in bars:
        signal = adapter.on_bar(bar, _STATE)
        record = adapter.last_stop_record
        if signal is not None and signal.kind is SignalKind.ENTRY:
            positions.append([])
            holding = True
        if holding and record is not None:
            positions[-1].append((signal, record))
        if signal is not None and signal.kind is SignalKind.EXIT:
            holding = False
    return positions


class TestNewPositionStartsFresh:
    """A new position never inherits the stop of the one before it."""

    def test_the_next_entry_stop_is_its_own_raw_level(self) -> None:
        """After a crash exit at a high stop, the re-entry starts at its own lower level."""
        steps = [Decimal("-300")] + [Decimal("40")] * 12
        positions = _positions(_path(steps))
        assert len(positions) >= 2, "the path must open a second position"
        old_stop = positions[0][-1][1].stop_price

        signal, record = positions[1][0]

        assert signal is not None
        assert record.chandelier_level < old_stop
        assert (signal.stop_price, record.stop_price, record.stop_moved) == (
            record.chandelier_level,
            record.chandelier_level,
            None,
        )


_steps = st.lists(
    st.decimals(min_value=Decimal("-60"), max_value=Decimal("40"), places=2),
    min_size=5,
    max_size=60,
)


class TestStopNeverFallsInAPosition:
    """Property: over any price path, the stop in force never decreases while held."""

    @given(steps=_steps)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_the_stop_in_force_never_decreases_bar_to_bar(self, steps: list[Decimal]) -> None:
        """Each held bar's stop is at least the previous held bar's stop of the same position."""
        for position in _positions(_path(steps)):
            stops = [record.stop_price for _, record in position]
            assert stops == sorted(stops)


class TestRetryAfterADeclinedExitKeepsTheStop:
    """A declined exit restored without ``last_stop`` keeps the stop in force (#706, #680)."""

    def test_the_retry_bar_exits_at_the_held_stop_under_a_lower_level(self) -> None:
        """Exit at 95 declined on bar 17, then low 94.8: the retry fires at 95, not at 92.3.

        The declined bar ends a day, so its level 92.3 (TR 6.5, ATR 2.9) is
        posted before the retry bar is tested, and ratchets under the held 95.
        Replay and the parity check restore the held position with no stored
        stop; the adapter keeps the stop it recorded for that entry, so the
        retry decides as serve does (ADR-011).
        """
        adapter = _held(None)
        for index in range(_FLAT_BARS, _DAY_END):
            _record(adapter, _h4(index))
        declined, _ = _record(adapter, _h4(_DAY_END, low="94.5"))
        assert declined is not None
        adapter.restore_position(
            OpenPosition(entry_time=_h4(_ENTRY_INDEX).close_time, entry_price=Decimal("100"))
        )

        signal, record = _record(adapter, _h4(_DAY_END + 1, low="94.8"))

        assert signal is not None
        assert (signal.exit_reason, signal.stop_price, record.chandelier_level) == (
            "chandelier",
            Decimal("95"),
            Decimal("92.3"),
        )
