"""Boundary tests for ``SwingStrategyAdapter`` entry and exit rules.

Each test sits on one edge of a pre-registered rule, where a ``<`` written
as ``<=`` (or a fallback written wrong) changes the signal:

- the Chandelier exit fires when the bar's low equals the stop (§5.2 A);
- the time stop fires after 90 days, not only on the exact bar (§5.2 B);
- a daily bar closing at the same instant as the 4h bar counts for the
  regime (§5.2 C);
- an exit with no computable Chandelier level or ATR still carries the
  documented fallback stop, close - ATR with ATR taken as 1;
- an entry whose stop is not below the entry price, or whose sizing ATR is
  missing, is not emitted (R-7);
- ADX exactly at the threshold counts as momentum (ADX >= 25, §5.1);
- a stop recorded for one position is never carried into another (#680);
- the stop record's highest high counts the bar that closes at entry (#388);
- bars of intervals other than 4h and 1d change nothing.

Synthetic bars only. Reference: pre-registration-v1 §5.1-§5.3.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.strategy.swing.engines.adx import AdxEngine
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_H4_T0 = _T0 + timedelta(days=20)
_SPAN = {
    KlineInterval.M5: timedelta(minutes=5),
    KlineInterval.H1: timedelta(hours=1),
    KlineInterval.H4: timedelta(hours=4),
    KlineInterval.D1: timedelta(days=1),
}
_STATE = PortfolioState(
    peak_value=Decimal("10000"),
    current_value=Decimal("10000"),
    realized_slippage_cumulative=Decimal("0"),
    correlations={},
    dd_halt_required=False,
)


def _bar(
    close: str,
    index: int,
    *,
    interval: KlineInterval = KlineInterval.H4,
    high: str | None = None,
    low: str | None = None,
) -> Bar:
    """Build a closed bar; 4h bars start on day 20, daily and others on day 0."""
    span = _SPAN[interval]
    open_time = (_H4_T0 if interval is KlineInterval.H4 else _T0) + span * index
    price = Decimal(close)
    return Bar(
        pair=_PAIR,
        interval=interval,
        open_time=open_time,
        open=price,
        high=Decimal(high) if high is not None else price + Decimal("2"),
        low=Decimal(low) if low is not None else price - Decimal("2"),
        close=price,
        volume=Decimal("100"),
        close_time=open_time + span,
    )


def _adapter(
    *,
    adx_threshold: Decimal = Decimal("5"),
    chandelier_period: int = 5,
    chandelier_atr_mult: Decimal = Decimal("3.0"),
    atr_sizing_period: int = 5,
) -> SwingStrategyAdapter:
    """Return an adapter with small periods, as the other swing unit tests use."""
    return SwingStrategyAdapter(
        donchian_period=5,
        adx_period=5,
        adx_threshold=adx_threshold,
        ema_period=10,
        chandelier_period=chandelier_period,
        chandelier_atr_mult=chandelier_atr_mult,
        atr_sizing_period=atr_sizing_period,
    )


def _rising_daily() -> list[Bar]:
    """Twelve rising daily bars, all closed before the first 4h bar: regime is up."""
    return [_bar(str(1000 + 200 * i // 11), i, interval=KlineInterval.D1) for i in range(12)]


def _flat_4h(count: int = 11) -> list[Bar]:
    """Flat 4h bars at 1000 (high 1002, low 998): no breakout."""
    return [_bar("1000", i) for i in range(count)]


def _exit_of(signal: StrategySignal | None) -> tuple[SignalKind, str | None, Decimal]:
    """Return the kind, reason and stop of ``signal``, which must be a signal."""
    assert isinstance(signal, StrategySignal), f"expected a signal, got {signal!r}"
    return signal.kind, signal.exit_reason, signal.stop_price


class TestChandelierExitBoundary:
    """§5.2 A: the position exits when the bar's low reaches the stop in force."""

    def test_a_low_exactly_at_the_stop_exits(self) -> None:
        """A bar whose low equals the stop in force (95) exits on the Chandelier.

        Six flat bars (high 1002, low 998, TR 4) give a level of 1002 - 3 x 4 = 990
        with Chandelier(5); the restored stop in force is 995, above it. The next
        bar has low 995: TR 7 lowers the level further, so the stop stays 995 and
        the low touches it exactly.
        """
        adapter = _adapter()
        adapter.seed(_flat_4h(6))
        adapter.restore_position(
            OpenPosition(
                entry_time=_flat_4h(6)[-1].close_time,
                entry_price=Decimal("1000"),
                last_stop=Decimal("995"),
            )
        )

        signal = adapter.on_bar(_bar("1000", 6, low="995"), _STATE)

        assert _exit_of(signal) == (SignalKind.EXIT, "chandelier", Decimal("995"))


class TestFallbackExitStop:
    """With no Chandelier level and no ATR, exits carry close - 1 as their stop."""

    def test_time_stop_without_history_uses_close_minus_one(self) -> None:
        """A time-stop exit with no computable level or ATR carries stop = close - 1.

        Periods of 50 cannot be met by one bar, so the Chandelier level and the
        sizing ATR are both missing; the adapter then takes ATR as 1. The bar
        closes 90 days and 4 hours after entry, past the time stop, not on it.
        """
        bar = _bar("1000", 0)
        adapter = _adapter(chandelier_period=50, atr_sizing_period=50)
        adapter.restore_position(
            OpenPosition(
                entry_time=bar.close_time - timedelta(days=90, hours=4),
                entry_price=Decimal("900"),
            )
        )

        signal = adapter.on_bar(bar, _STATE)

        assert _exit_of(signal) == (SignalKind.EXIT, "time_stop", Decimal("999"))

    def test_regime_break_without_history_uses_close_minus_one(self) -> None:
        """A regime-break exit with no level or ATR carries stop = close - 1.

        Twelve rising daily closes (1000 to 1200, regime up), then a daily close of
        500 that ends at the same instant as the 4h bar: it counts as closed, and
        500 is below EMA(10), so the regime breaks. A rule that only counted daily
        bars closed strictly before the 4h bar would still see the regime up.
        """
        bar = _bar("1000", 0)
        daily = _rising_daily()
        crash_open = bar.close_time - timedelta(days=1)
        crash = Bar(
            pair=_PAIR,
            interval=KlineInterval.D1,
            open_time=crash_open,
            open=Decimal("500"),
            high=Decimal("502"),
            low=Decimal("498"),
            close=Decimal("500"),
            volume=Decimal("100"),
            close_time=bar.close_time,
        )
        adapter = _adapter(chandelier_period=50, atr_sizing_period=50)
        adapter.seed([*daily, crash])
        adapter.restore_position(
            OpenPosition(entry_time=bar.close_time - timedelta(days=1), entry_price=Decimal("900"))
        )

        signal = adapter.on_bar(bar, _STATE)

        assert _exit_of(signal) == (SignalKind.EXIT, "regime_break", Decimal("999"))


def _breakout_history() -> list[Bar]:
    """Rising daily bars and eleven flat 4h bars: the next 4h close above 1002 breaks out."""
    return [*_rising_daily(), *_flat_4h()]


class TestEntryGuard:
    """R-7: an entry is emitted only with a stop strictly below entry and a sizing ATR."""

    @pytest.mark.parametrize("high", ["2000", "2001"])
    def test_a_stop_not_below_the_entry_price_refuses_the_entry(self, high: str) -> None:
        """With multiplier 0 the stop is the highest high: at or above the 2000 close, no entry.

        The breakout bar closes at 2000 with high 2000 (stop equals entry) or 2001
        (stop above entry); the Chandelier(5) stop with multiplier 0 is that high.
        All three entry conditions hold, so only the stop guard refuses it.
        """
        adapter = _adapter(chandelier_atr_mult=Decimal("0"))
        adapter.seed(_breakout_history())

        signal = adapter.on_bar(_bar("2000", 11, high=high), _STATE)

        check = adapter.last_entry_check
        assert (signal, check is not None and check.all_met) == (None, True)

    def test_a_missing_sizing_atr_refuses_the_entry(self) -> None:
        """With a sizing ATR period of 50 there is no ATR to size from: no entry.

        The Chandelier(5) stop is computable and below the 2000 close, and all
        three entry conditions hold, so only the missing ATR refuses it.
        """
        adapter = _adapter(atr_sizing_period=50)
        adapter.seed(_breakout_history())

        signal = adapter.on_bar(_bar("2000", 11), _STATE)

        assert (
            signal,
            adapter.last_entry_check is not None and adapter.last_entry_check.all_met,
        ) == (
            None,
            True,
        )


def test_adx_exactly_at_the_threshold_counts_as_momentum() -> None:
    """§5.1 ADX >= threshold: with the threshold set to the bar's own ADX, momentum holds."""
    history = _breakout_history()
    breakout = _bar("2000", 11)
    adx = AdxEngine.value([*_flat_4h(), breakout], period=5)
    assert adx is not None, "ADX(5) must be computable on 12 4h bars"
    adapter = _adapter(adx_threshold=adx)
    adapter.seed(history)

    adapter.on_bar(breakout, _STATE)

    check = adapter.last_entry_check
    assert check is not None and check.momentum is True, (
        f"ADX {adx} equal to the threshold must count as momentum, got {check}"
    )


@pytest.mark.parametrize("shift", [timedelta(hours=-4), timedelta(hours=4)])
def test_a_stop_recorded_for_another_entry_is_not_carried_over(shift: timedelta) -> None:
    """Restoring a position with a different entry time starts its stop afresh (#680).

    The adapter first holds a position entered at the sixth bar's close and
    records its stop on the seventh bar. Restoring a position entered 4 hours
    earlier or later, with no stored stop, must not ratchet from that record:
    the next bar's record has no previous stop, so ``stop_moved`` is ``None``.
    """
    bars = _flat_4h(8)
    first_entry = bars[5].close_time
    adapter = _adapter()
    adapter.seed(bars[:6])
    adapter.restore_position(OpenPosition(entry_time=first_entry, entry_price=Decimal("1000")))
    adapter.on_bar(bars[6], _STATE)
    adapter.restore_position(
        OpenPosition(entry_time=first_entry + shift, entry_price=Decimal("1000"))
    )

    adapter.on_bar(bars[7], _STATE)

    record = adapter.last_stop_record
    assert record is not None and record.stop_moved is None, (
        f"a fresh position must start without a previous stop, got {record}"
    )


def test_high_since_entry_counts_the_bar_closing_at_entry() -> None:
    """The stop record's highest high counts the oldest buffered bar when it closes at entry.

    Six seeded bars, the first with high 1050; the entry time is that first
    bar's close. The next bar (high 1002) is held, so the highest high since
    entry is 1050.
    """
    seeded = [_bar("1000", 0, high="1050"), *_flat_4h(6)[1:]]
    adapter = _adapter()
    adapter.seed(seeded)
    adapter.restore_position(
        OpenPosition(entry_time=seeded[0].close_time, entry_price=Decimal("1000"))
    )

    adapter.on_bar(_bar("1000", 6), _STATE)

    record = adapter.last_stop_record
    assert record is not None and record.high_since_entry == Decimal("1050"), (
        f"highest high since entry must be 1050, got {record}"
    )


class TestOtherIntervalsAreIgnored:
    """Only 4h and 1d bars reach the strategy; 5m and 1h bars change nothing."""

    def _noise(self) -> list[Bar]:
        """Return a 5m breakout far above the channel and a 1h crash far below the EMA."""
        return [
            _bar("5000", 0, interval=KlineInterval.M5),
            _bar("10", 0, interval=KlineInterval.H1),
        ]

    def test_on_bar_ignores_other_intervals(self) -> None:
        """Fed 5m and 1h bars, ``on_bar`` emits nothing and the next 4h entry is unchanged."""
        noisy = _adapter()
        control = _adapter()
        for bar in _breakout_history():
            noisy.on_bar(bar, _STATE)
            control.on_bar(bar, _STATE)

        noise_signals = [noisy.on_bar(bar, _STATE) for bar in self._noise()]
        expected = control.on_bar(_bar("2000", 11), _STATE)
        assert isinstance(expected, StrategySignal), "the control adapter must enter at 2000"

        assert (noise_signals, noisy.on_bar(_bar("2000", 11), _STATE)) == (
            [None, None],
            expected,
        )

    def test_seed_ignores_other_intervals(self) -> None:
        """Seeded with 5m and 1h bars among the history, the next 4h entry is unchanged."""
        noisy = _adapter()
        control = _adapter()
        noisy.seed([*_breakout_history(), *self._noise()])
        control.seed(_breakout_history())
        expected = control.on_bar(_bar("2000", 11), _STATE)
        assert isinstance(expected, StrategySignal), "the control adapter must enter at 2000"

        assert noisy.on_bar(_bar("2000", 11), _STATE) == expected
