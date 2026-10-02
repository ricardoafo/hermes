"""Regression and property tests for issue #311: 1D bars must not leak before they close.

Invariant tested:
    A 4h signal only depends on 1D bars whose ``close_time`` is at or before
    the 4h bar's ``close_time``, whatever order the caller delivers the two
    streams in.  Delivering a 1D bar early (for example by merging the streams
    by ``open_time``) must produce exactly the signals of the canonical
    close-time ordering.

Reference: issue #311; spec-5-swing-strategy-adapter change.md R-8;
openspec/changes/311-daily-bar-look-ahead/change.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_SEED_DAYS = 12
_H4_START_DAY = 10


def _bar(close: Decimal, open_time: datetime, interval: KlineInterval) -> Bar:
    hours = 4 if interval == KlineInterval.H4 else 24
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


def _adapter() -> SwingStrategyAdapter:
    return SwingStrategyAdapter(
        donchian_period=5,
        adx_period=5,
        adx_threshold=Decimal("5"),
        ema_period=10,
        chandelier_period=5,
        atr_sizing_period=5,
    )


def _state() -> PortfolioState:
    return PortfolioState(
        peak_value=Decimal("10000"),
        current_value=Decimal("10000"),
        realized_slippage_cumulative=Decimal("0"),
        correlations={},
        dd_halt_required=False,
    )


def _replay(bars: list[Bar]) -> list[tuple[datetime, StrategySignal]]:
    adapter = _adapter()
    state = _state()
    signals = []
    for bar in bars:
        sig = adapter.on_bar(bar, state)
        if sig is not None:
            signals.append((bar.close_time, sig))
    return signals


def _by_close_time(bars: list[Bar]) -> list[Bar]:
    return sorted(bars, key=lambda b: (b.close_time, b.interval != KlineInterval.D1))


def _bearish_days_then_spike() -> list[Bar]:
    """Declining 1D closes (regime bearish), then one day whose final close spikes."""
    days = [
        _bar(Decimal("1000") - Decimal(i), _BASE + timedelta(days=i), KlineInterval.D1)
        for i in range(_SEED_DAYS)
    ]
    days.append(_bar(Decimal("5000"), _BASE + timedelta(days=_SEED_DAYS), KlineInterval.D1))
    return days


def _flat_then_breakout_4h(breakout_at: datetime) -> list[Bar]:
    """Flat 4h bars from day 10 to day 14, jumping to 2000 from ``breakout_at``."""
    start = _BASE + timedelta(days=_H4_START_DAY)
    bars = []
    for i in range(6 * 4):
        open_time = start + timedelta(hours=4 * i)
        close = Decimal("2000") if open_time >= breakout_at else Decimal("1000")
        bars.append(_bar(close, open_time, KlineInterval.H4))
    return bars


class TestDailyBarDeliveredBeforeItCloses:
    """Regression for #311: an open_time merge must not leak the day's close."""

    def test_open_time_merge_emits_same_signals_as_close_time_merge(self) -> None:
        """Signals must not change when the spike day is delivered at its open.

        The 1D regime only turns bullish on the spike day's close (24:00).  The
        4h breakout fires at 08:00 that day.  With the daily bar delivered at
        its open, the pre-fix adapter entered at 12:00 using a close 12 hours
        in the future.
        """
        spike_day = _BASE + timedelta(days=_SEED_DAYS)
        bars = _bearish_days_then_spike() + _flat_then_breakout_4h(spike_day + timedelta(hours=8))

        open_time_order = sorted(bars, key=lambda b: b.open_time)

        assert _replay(open_time_order) == _replay(_by_close_time(bars)), (
            "open_time delivery changed the signals: a 1D bar was read before it closed"
        )

    def test_no_entry_before_the_regime_bar_closes(self) -> None:
        """No signal may be emitted before the only bullish 1D bar has closed."""
        spike_day = _BASE + timedelta(days=_SEED_DAYS)
        bars = _bearish_days_then_spike() + _flat_then_breakout_4h(spike_day + timedelta(hours=8))

        signals = _replay(sorted(bars, key=lambda b: b.open_time))

        early = [t for t, _ in signals if t < spike_day + timedelta(days=1)]
        assert early == [], f"signals at {early} used the spike day's close before 24:00"


@st.composite
def _early_daily_interleaving(draw: st.DrawFn) -> tuple[list[Bar], list[Bar]]:
    """Return (canonical stream, same bars with 1D bars delivered early).

    Each 1D bar is moved to a random position no later than its canonical
    one, preserving the order within each interval.
    """
    daily_closes = draw(
        st.lists(
            st.decimals(min_value=Decimal("500"), max_value=Decimal("1500"), places=0),
            min_size=_SEED_DAYS + 3,
            max_size=_SEED_DAYS + 3,
        )
    )
    four_hour_closes = draw(
        st.lists(
            st.decimals(min_value=Decimal("800"), max_value=Decimal("1200"), places=0),
            min_size=6 * 5,
            max_size=6 * 5,
        )
    )
    days = [
        _bar(c, _BASE + timedelta(days=i), KlineInterval.D1) for i, c in enumerate(daily_closes)
    ]
    start = _BASE + timedelta(days=_H4_START_DAY)
    four_hours = [
        _bar(c, start + timedelta(hours=4 * i), KlineInterval.H4)
        for i, c in enumerate(four_hour_closes)
    ]
    canonical = _by_close_time(days + four_hours)

    # Number of 4h bars delivered before each 1D bar, capped at its canonical count.
    canonical_counts = [sum(1 for h in four_hours if h.close_time < d.close_time) for d in days]
    offsets = sorted(draw(st.integers(min_value=0, max_value=c)) for c in canonical_counts)
    early: list[Bar] = []
    day_iter = iter(zip(offsets, days, strict=True))
    pending = next(day_iter, None)
    for n_delivered, h in enumerate([*four_hours, None]):
        while pending is not None and pending[0] <= n_delivered:
            early.append(pending[1])
            pending = next(day_iter, None)
        if h is not None:
            early.append(h)
    return canonical, early


class TestAnyEarlyDeliveryIsHarmless:
    """Property for #311: no signal depends on a 1D bar that closed after it."""

    @given(streams=_early_daily_interleaving())
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_early_daily_delivery_matches_canonical_signals(
        self, streams: tuple[list[Bar], list[Bar]]
    ) -> None:
        """Delivering 1D bars early must reproduce the close-time-ordered signals exactly."""
        canonical, early = streams
        assert len(early) == len(canonical)
        assert _replay(early) == _replay(canonical)
