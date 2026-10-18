"""Unit tests for DailyMaAdapter, batch swing-b1 candidate C2 (issue #1064).

Rules under test (research batch swing-b1 §C2, card swing-b1-daily-ma):

- Decide only on the 4h bar closing at 00:00 UTC, from the daily close
  completed at that instant.
- Entry when flat: the close is above SMA(``sma_period``) and the previous
  close was at or below its SMA, or the position was closed since the last
  close at or below the SMA.
- Exit: a 4h low at or below the stop in force when the bar opened; or, on the
  00:00 bar, a daily close below the SMA.
- Stop: the signal bar's close minus ``stop_atr_mult`` x daily ATR(22), then at
  each daily close the close minus that distance if higher, never lower.

Fixture arithmetic: every daily bar has high = close + 1 and low = close - 1,
and consecutive closes move by at most 1, so every true range is 2 and the
daily ATR(22) is exactly 2. At the base multiple 3 the stop sits 6 under its
reference close. Tests use ``sma_period=3`` so SMAs are worked by hand; 30 flat
days at 100 come first so the ATR has its 23 bars.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.strategy_protocol.strategy_factory import (
    DAILY_MA,
    known_signals,
    strategy_kind,
)
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.swing.daily_ma import MA_BREAK, PROTECTIVE_STOP, DailyMaAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config import StrategiesConfig
from hermes.infrastructure.config.loader import load_yaml_model

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_DAY0 = datetime(2021, 1, 1, tzinfo=UTC)
_FLAT_DAYS = 30
_FLAT = Decimal("100")


def _daily(
    day: int, close: Decimal, *, high: Decimal | None = None, low: Decimal | None = None
) -> Bar:
    open_time = _DAY0 + timedelta(days=day)
    return Bar(
        pair=_PAIR,
        interval=KlineInterval.D1,
        open_time=open_time,
        open=close,
        high=close + 1 if high is None else high,
        low=close - 1 if low is None else low,
        close=close,
        volume=Decimal("100"),
        close_time=open_time + timedelta(days=1),
    )


def _h4(day: int, slot: int, close: Decimal, *, low: Decimal | None = None) -> Bar:
    """Build the 4h bar ``slot`` (0-5) of ``day``; slot 5 closes at 00:00 UTC the next day."""
    open_time = _DAY0 + timedelta(days=day, hours=4 * slot)
    bar_low = close - Decimal("0.5") if low is None else low
    return Bar(
        pair=_PAIR,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=close,
        high=close + Decimal("0.5"),
        low=bar_low,
        close=close,
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=4),
    )


def _state() -> PortfolioState:
    return PortfolioState(
        peak_value=Decimal("5000"),
        current_value=Decimal("5000"),
        realized_slippage_cumulative=Decimal("0"),
        correlations={},
        dd_halt_required=False,
    )


def _seeded(*, sma_period: int = 3, stop_atr_mult: Decimal = Decimal("3.0")) -> DailyMaAdapter:
    """Build an adapter (``sma_period=3`` unless given) holding 30 flat days at 100."""
    adapter = DailyMaAdapter(sma_period=sma_period, stop_atr_mult=stop_atr_mult)
    adapter.seed([_daily(d, _FLAT) for d in range(_FLAT_DAYS)])
    return adapter


def _close_day(adapter: DailyMaAdapter, day: int, close: Decimal) -> StrategySignal | None:
    """Deliver ``day``'s daily bar, then its 00:00 UTC 4h bar at the same close."""
    adapter.on_bar(_daily(day, close), _state())
    return adapter.on_bar(_h4(day, 5, close), _state())


def _entered(adapter: DailyMaAdapter) -> StrategySignal | None:
    """Day 30 closes at 101: SMA3 = 100.33 is crossed from 100 <= 100, so C2 enters."""
    return _close_day(adapter, _FLAT_DAYS, Decimal("101"))


def _seeded_closes(
    closes: list[Decimal], *, sma_period: int = 3, stop_atr_mult: Decimal = Decimal("3.0")
) -> DailyMaAdapter:
    """Build an adapter holding one daily bar per close, from day 0."""
    adapter = DailyMaAdapter(sma_period=sma_period, stop_atr_mult=stop_atr_mult)
    adapter.seed([_daily(d, c) for d, c in enumerate(closes)])
    return adapter


def _kind(signal: StrategySignal | None) -> str:
    """Name what a bar emitted: ``"none"``, ``"entry"`` or ``"exit"``."""
    return "none" if signal is None else signal.kind.value


def _decide(adapter: DailyMaAdapter, day: int, close: Decimal) -> str:
    """Close ``day`` and name what its midnight bar emitted."""
    return _kind(_close_day(adapter, day, close))


def _other(interval: KlineInterval, close_time: datetime, close: Decimal) -> Bar:
    """Build a bar of ``interval`` closing at ``close_time``."""
    width = {
        KlineInterval.M1: timedelta(minutes=1),
        KlineInterval.M5: timedelta(minutes=5),
        KlineInterval.H1: timedelta(hours=1),
    }[interval]
    return Bar(
        pair=_PAIR,
        interval=interval,
        open_time=close_time - width,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=Decimal("100"),
        close_time=close_time,
    )


def _summary(signal: StrategySignal | None) -> tuple[object, ...] | None:
    if signal is None:
        return None
    return (signal.kind, signal.entry_price, signal.stop_price, signal.exit_reason)


class TestEntry:
    """A fresh cross above the SMA, taken on the 00:00 UTC bar."""

    # SEV-1: the candidate must trade its signed entry rule (batch swing-b1 §C2)
    def test_a_fresh_cross_enters_with_the_stop_six_under_the_close(self) -> None:
        """Close 101 over SMA3 100.33, previous close 100 <= 100: entry, stop 101 - 3 x 2 = 95."""
        assert _summary(_entered(_seeded())) == (
            SignalKind.ENTRY,
            Decimal("101"),
            Decimal("95"),
            None,
        )

    def test_a_close_equal_to_its_sma_does_not_enter(self) -> None:
        """Flat closes sit on their SMA: not above it, so no entry."""
        assert _kind(_close_day(_seeded(), _FLAT_DAYS, _FLAT)) == "none"

    def test_a_close_above_after_a_close_above_is_not_a_cross(self) -> None:
        """Day 30's decision bar is missed; day 31's previous close 101 > 100.33, so no entry."""
        adapter = _seeded()
        adapter.on_bar(_daily(_FLAT_DAYS, Decimal("101")), _state())
        assert _kind(_close_day(adapter, _FLAT_DAYS + 1, Decimal("102"))) == "none"

    def test_a_4h_bar_not_closing_at_midnight_never_decides(self) -> None:
        """The cross holds, but the 20:00 UTC bar is not the decision bar."""
        adapter = _seeded()
        adapter.on_bar(_daily(_FLAT_DAYS, Decimal("101")), _state())
        assert _kind(adapter.on_bar(_h4(_FLAT_DAYS + 1, 0, Decimal("101")), _state())) == "none"

    def test_a_daily_bar_closing_after_the_4h_bar_is_not_read(self) -> None:
        """No lookahead: day 31's daily bar, delivered early, does not decide day 30's bar."""
        adapter = _seeded()
        adapter.on_bar(_daily(_FLAT_DAYS, _FLAT), _state())
        adapter.on_bar(_daily(_FLAT_DAYS + 1, Decimal("101")), _state())
        assert _kind(adapter.on_bar(_h4(_FLAT_DAYS, 5, _FLAT), _state())) == "none"

    def test_sizing_reads_the_larger_multiple(self) -> None:
        """At the 3.5 neighbour the stop is 101 - 3.5 x 2 = 94."""
        signal = _entered(_seeded(stop_atr_mult=Decimal("3.5")))
        assert _summary(signal) == (SignalKind.ENTRY, Decimal("101"), Decimal("94"), None)


class TestExit:
    """The protective stop, then the moving-average break."""

    # SEV-1: the protective stop must close the position (batch swing-b1 §C2)
    def test_a_low_on_the_stop_exits(self) -> None:
        """Entry stop 95; a 4h low of exactly 95 on day 31 exits."""
        adapter = _seeded()
        _entered(adapter)
        signal = adapter.on_bar(_h4(_FLAT_DAYS + 1, 1, Decimal("100"), low=Decimal("95")), _state())
        assert _summary(signal) == (SignalKind.EXIT, Decimal("101"), Decimal("95"), PROTECTIVE_STOP)

    def test_a_low_just_above_the_stop_holds(self) -> None:
        """A 4h low of 95.01 stays above the 95 stop."""
        adapter = _seeded()
        _entered(adapter)
        low = Decimal("95.01")
        assert (
            _kind(adapter.on_bar(_h4(_FLAT_DAYS + 1, 1, Decimal("100"), low=low), _state()))
            == "none"
        )

    # SEV-1: a daily close under the SMA must close the position (batch swing-b1 §C2)
    def test_a_daily_close_below_the_sma_exits_on_the_midnight_bar(self) -> None:
        """Day 31 closes at 100: SMA3 = (100 + 101 + 100) / 3 = 100.33 > 100."""
        adapter = _seeded()
        _entered(adapter)
        signal = _close_day(adapter, _FLAT_DAYS + 1, _FLAT)
        assert _summary(signal) == (SignalKind.EXIT, Decimal("101"), Decimal("95"), MA_BREAK)

    def test_a_daily_close_on_the_sma_holds(self) -> None:
        """Day 31 closes at 100.5: SMA3 = (100 + 101 + 100.5) / 3 = 100.5, not below, no exit."""
        adapter = _seeded()
        _entered(adapter)
        assert _kind(_close_day(adapter, _FLAT_DAYS + 1, Decimal("100.5"))) == "none"

    def test_an_exit_carries_the_daily_atr(self) -> None:
        """The exit's atr_context is the daily ATR(22) at the exit bar, 2."""
        adapter = _seeded()
        _entered(adapter)
        signal = adapter.on_bar(_h4(_FLAT_DAYS + 1, 1, Decimal("100"), low=Decimal("95")), _state())
        summary = None if signal is None else (signal.kind, signal.atr_context)
        assert summary == (SignalKind.EXIT, Decimal(2))


class TestStopRatchet:
    """The stop is raised at each daily close, never lowered."""

    def test_a_daily_close_raises_the_stop_for_the_next_day(self) -> None:
        """Day 31 closes at 101.9: level 101.9 - 6 = 95.9 > 95, so a day-32 low of 95.5 exits."""
        adapter = _seeded()
        _entered(adapter)
        _close_day(adapter, _FLAT_DAYS + 1, Decimal("101.9"))
        signal = adapter.on_bar(
            _h4(_FLAT_DAYS + 2, 1, Decimal("101"), low=Decimal("95.5")), _state()
        )
        assert _summary(signal) == (
            SignalKind.EXIT,
            Decimal("101"),
            Decimal("95.9"),
            PROTECTIVE_STOP,
        )

    def test_the_midnight_bar_is_tested_against_the_stop_before_its_close(self) -> None:
        """Day 31's own midnight bar has low 95.5: the 95.9 level applies only after it."""
        adapter = _seeded()
        _entered(adapter)
        adapter.on_bar(_daily(_FLAT_DAYS + 1, Decimal("101.9")), _state())
        bar = _h4(_FLAT_DAYS + 1, 5, Decimal("101.9"), low=Decimal("95.5"))
        assert _kind(adapter.on_bar(bar, _state())) == "none"

    def test_a_lower_level_never_lowers_the_stop(self) -> None:
        """A wide day 31 (high 106, low 97) lifts ATR to (2 x 21 + 9) / 22 = 2.318.

        Its level 101.5 - 3 x 2.318 = 94.55 is below 95, so the stop stays 95
        and a day-32 low of 94.8 exits.
        """
        adapter = _seeded()
        _entered(adapter)
        wide = _daily(_FLAT_DAYS + 1, Decimal("101.5"), high=Decimal("106"), low=Decimal("97"))
        adapter.on_bar(wide, _state())
        adapter.on_bar(_h4(_FLAT_DAYS + 1, 5, Decimal("101.5")), _state())
        signal = adapter.on_bar(
            _h4(_FLAT_DAYS + 2, 1, Decimal("101"), low=Decimal("94.8")), _state()
        )
        assert _summary(signal) == (SignalKind.EXIT, Decimal("101"), Decimal("95"), PROTECTIVE_STOP)


class TestReEntry:
    """After an exit, a close above the SMA re-enters until a close at or below it."""

    def test_a_stop_exit_allows_reentry_without_a_fresh_cross(self) -> None:
        """Stopped intraday on day 31; its close 101.5 is above SMA3 100.83 but not a cross.

        The previous close 101 is above its SMA3 100.33, so only the exit
        allows the entry; the stop is 101.5 - 3 x 2 = 95.5.
        """
        adapter = _seeded()
        _entered(adapter)
        adapter.on_bar(_h4(_FLAT_DAYS + 1, 1, Decimal("100"), low=Decimal("95")), _state())
        signal = _close_day(adapter, _FLAT_DAYS + 1, Decimal("101.5"))
        assert _summary(signal) == (SignalKind.ENTRY, Decimal("101.5"), Decimal("95.5"), None)

    def test_a_close_below_the_sma_after_the_exit_ends_the_reentry_right(self) -> None:
        """Stopped on day 31, which closes at 100 under SMA3 100.33.

        Day 32 closes at 101 but its decision bar is missed. Day 33 closes at
        101.5 above SMA3 100.83, after 101 above SMA3 100.67: not a cross, and
        the day-31 close spent the re-entry right, so no entry.
        """
        adapter = _seeded()
        _entered(adapter)
        adapter.on_bar(_h4(_FLAT_DAYS + 1, 1, Decimal("100"), low=Decimal("95")), _state())
        _close_day(adapter, _FLAT_DAYS + 1, _FLAT)
        adapter.on_bar(_daily(_FLAT_DAYS + 2, Decimal("101")), _state())
        assert _kind(_close_day(adapter, _FLAT_DAYS + 3, Decimal("101.5"))) == "none"


class TestRestore:
    """Restored positions keep their stop, as the StrategyPort contract asks."""

    def test_a_retried_exit_keeps_the_higher_stop(self) -> None:
        """An unfilled exit is restored with last_stop 96: the stop in force is 96, not 95."""
        adapter = _seeded()
        _entered(adapter)
        entry_time = _h4(_FLAT_DAYS, 5, Decimal("101")).close_time
        adapter.restore_position(
            OpenPosition(entry_time=entry_time, entry_price=Decimal("101"), last_stop=Decimal("96"))
        )
        signal = adapter.on_bar(
            _h4(_FLAT_DAYS + 1, 1, Decimal("100"), low=Decimal("95.5")), _state()
        )
        assert _summary(signal) == (SignalKind.EXIT, Decimal("101"), Decimal("96"), PROTECTIVE_STOP)

    def test_a_restart_without_a_stored_stop_posts_it_from_the_entry_price(self) -> None:
        """A new adapter restored at entry 101 with no stop: 101 - 3 x 2 = 95."""
        adapter = DailyMaAdapter(sma_period=3)
        dailies = [_daily(d, _FLAT) for d in range(_FLAT_DAYS)]
        dailies.append(_daily(_FLAT_DAYS, Decimal("101")))
        entry_time = _h4(_FLAT_DAYS, 5, Decimal("101")).close_time
        adapter.seed(dailies, OpenPosition(entry_time=entry_time, entry_price=Decimal("101")))
        signal = adapter.on_bar(_h4(_FLAT_DAYS + 1, 1, Decimal("100"), low=Decimal("95")), _state())
        assert _summary(signal) == (SignalKind.EXIT, Decimal("101"), Decimal("95"), PROTECTIVE_STOP)

    def test_an_unfilled_entry_leaves_the_adapter_flat(self) -> None:
        """Restored flat after the entry: a low under the old stop emits nothing."""
        adapter = _seeded()
        _entered(adapter)
        adapter.restore_position(None)
        signal = adapter.on_bar(_h4(_FLAT_DAYS + 1, 1, Decimal("100"), low=Decimal("90")), _state())
        assert _kind(signal) == "none"


class TestParameters:
    """The kind's parameters are the batch's, at the signed base values."""

    def test_the_kind_is_registered(self) -> None:
        """``signal: daily_ma`` is a known signal."""
        assert (DAILY_MA in known_signals(), DAILY_MA) == (True, "daily_ma")

    def test_the_roster_entry_builds_this_kind(self) -> None:
        """``swing-b1-daily-ma`` in the shipped roster names ``signal: daily_ma``."""
        roster = load_yaml_model(
            Path(__file__).resolve().parents[5] / "config" / "strategies.yaml",
            StrategiesConfig,
            env_section="STRATEGIES",
            env={},
        )
        kinds = [
            s.strategy_kind().signal for s in roster.strategies if s.name == "swing-b1-daily-ma"
        ]
        assert kinds == [DAILY_MA]

    def test_the_defaults_are_the_signed_base_values(self) -> None:
        """sma_period 100 and stop_atr_mult 3.0 (batch swing-b1 §C2)."""
        assert strategy_kind(DAILY_MA).default_parameters() == {
            "sma_period": 100,
            "stop_atr_mult": Decimal("3.0"),
        }

    def test_every_neighbour_builds(self) -> None:
        """Each neighbour value of the batch builds an adapter of this kind."""
        kind = strategy_kind(DAILY_MA)
        neighbours: list[dict[str, int | Decimal]] = [
            {"sma_period": 50},
            {"sma_period": 150},
            {"stop_atr_mult": Decimal("2.5")},
            {"stop_atr_mult": Decimal("3.5")},
        ]
        built = [type(kind.create(n)).__name__ for n in neighbours]
        assert built == ["DailyMaAdapter"] * len(neighbours)

    def test_an_sma_of_one_close_is_refused(self) -> None:
        """An SMA of one close is the close itself: no cross is possible."""
        with pytest.raises(ValueError, match="sma_period must be at least 2"):
            DailyMaAdapter(sma_period=1)

    def test_an_sma_of_two_closes_is_accepted(self) -> None:
        """Two closes is the shortest SMA."""
        assert type(DailyMaAdapter(sma_period=2)) is DailyMaAdapter

    def test_a_non_positive_multiple_is_refused(self) -> None:
        """A stop at or above the entry would size an infinite position."""
        with pytest.raises(ValueError, match="stop_atr_mult must be positive"):
            DailyMaAdapter(stop_atr_mult=Decimal("0"))


_FLAT_N = [_FLAT] * _FLAT_DAYS
_OTHER_INTERVALS = [KlineInterval.M1, KlineInterval.M5, KlineInterval.H1]


class TestWarmUp:
    """The SMA and the ATR each need their full history before any entry."""

    def test_the_atr_needs_twenty_three_daily_bars(self) -> None:
        """22 flat days then a cross: 23 bars give ATR(22) = 2, so it enters with stop 95."""
        adapter = _seeded_closes([_FLAT] * 22, sma_period=2)
        signal = _close_day(adapter, 22, Decimal("101"))
        assert _summary(signal) == (SignalKind.ENTRY, Decimal("101"), Decimal("95"), None)

    def test_twenty_two_daily_bars_are_too_few_for_the_atr(self) -> None:
        """21 flat days then the same cross: 22 bars, no ATR(22), so no entry."""
        adapter = _seeded_closes([_FLAT] * 21, sma_period=2)
        assert _kind(_close_day(adapter, 21, Decimal("101"))) == "none"

    def test_the_cross_needs_sma_period_plus_one_closes(self) -> None:
        """SMA30 with 31 closes: the previous close has its own SMA30, so the cross enters."""
        adapter = _seeded_closes(_FLAT_N, sma_period=30)
        assert _decide(adapter, _FLAT_DAYS, Decimal("101")) == SignalKind.ENTRY.value

    def test_sma_period_closes_are_too_few_for_the_cross(self) -> None:
        """SMA30 with 30 closes: the previous close has no SMA30, so no entry."""
        adapter = _seeded_closes(_FLAT_N[:-1], sma_period=30)
        assert _decide(adapter, _FLAT_DAYS - 1, Decimal("101")) == "none"

    def test_a_long_history_keeps_enough_daily_bars_for_the_longest_sma(self) -> None:
        """400 flat days, then a cross of SMA150: the buffer still holds 151 closes."""
        adapter = _seeded_closes([_FLAT] * 400, sma_period=150)
        signal = _close_day(adapter, 400, Decimal("101"))
        assert _summary(signal) == (SignalKind.ENTRY, Decimal("101"), Decimal("95"), None)


class TestSmaWindow:
    """The SMA is the mean of the last ``sma_period`` closes, today's and the day before's."""

    def test_the_sma_is_of_the_last_closes_only(self) -> None:
        """Closes ..., 103, 102, 101, then 102.

        SMA3 before = (103 + 102 + 101) / 3 = 102 >= 101; SMA3 now =
        (102 + 101 + 102) / 3 = 101.67 < 102: a cross. An SMA of four closes
        ((103 + 102 + 101 + 102) / 4 = 102) or of every close (about 100.2,
        which 101 is above) would not cross.
        """
        closes = [_FLAT] * 27 + [Decimal("103"), Decimal("102"), Decimal("101")]
        assert _decide(_seeded_closes(closes), 30, Decimal("102")) == SignalKind.ENTRY.value

    def test_the_previous_close_is_judged_against_its_own_sma(self) -> None:
        """Closes ..., 100, 100, 103, 101.2, then 104.

        The previous close 101.2 is under its SMA3 (100 + 103 + 101.2) / 3 =
        101.4, so 104 over SMA3 102.73 is a cross. Against the SMA3 a day
        earlier, (100 + 100 + 103) / 3 = 101, it would not be.
        """
        closes = [_FLAT] * 28 + [Decimal("103"), Decimal("101.2")]
        assert _decide(_seeded_closes(closes), 30, Decimal("104")) == SignalKind.ENTRY.value

    def test_a_close_below_its_sma_does_not_enter(self) -> None:
        """Flat at 100, then 99: under SMA3 99.67, so no entry."""
        assert _decide(_seeded(), _FLAT_DAYS, Decimal("99")) == "none"


class TestDataEdges:
    """Missing, early and foreign bars never decide."""

    def test_a_missing_daily_bar_skips_that_days_decision(self) -> None:
        """Day 30 closes at 101 but its midnight bar is lost; day 31's daily bar is lost too.

        Day 31's midnight bar has no daily close at its instant, so it does
        not decide from day 30's close, which would be a cross.
        """
        adapter = _seeded()
        adapter.on_bar(_daily(_FLAT_DAYS, Decimal("101")), _state())
        assert _kind(adapter.on_bar(_h4(_FLAT_DAYS + 1, 5, Decimal("101")), _state())) == "none"

    def test_a_daily_bar_delivered_early_does_not_change_the_entry(self) -> None:
        """Day 31's wide bar (true range 9) arrives before day 30's midnight bar.

        The entry reads only bars closed by its instant: ATR 2, stop 95.
        """
        adapter = _seeded()
        adapter.on_bar(_daily(_FLAT_DAYS, Decimal("101")), _state())
        early = _daily(_FLAT_DAYS + 1, Decimal("101.5"), high=Decimal("106"), low=Decimal("97"))
        adapter.on_bar(early, _state())
        signal = adapter.on_bar(_h4(_FLAT_DAYS, 5, Decimal("101")), _state())
        assert _summary(signal) == (SignalKind.ENTRY, Decimal("101"), Decimal("95"), None)

    @pytest.mark.parametrize("interval", _OTHER_INTERVALS)
    def test_a_bar_of_another_interval_never_decides(self, interval: KlineInterval) -> None:
        """Day 30's cross is buffered; a non-4h bar closing at its midnight emits nothing."""
        adapter = _seeded()
        adapter.on_bar(_daily(_FLAT_DAYS, Decimal("101")), _state())
        close_time = _h4(_FLAT_DAYS, 5, Decimal("101")).close_time
        assert (
            _kind(adapter.on_bar(_other(interval, close_time, Decimal("101")), _state())) == "none"
        )

    @pytest.mark.parametrize("interval", _OTHER_INTERVALS)
    def test_a_bar_of_another_interval_is_not_a_daily_close(self, interval: KlineInterval) -> None:
        """A non-daily bar closing at day 30's midnight, at 101, does not stand in for day 30."""
        adapter = _seeded()
        close_time = _h4(_FLAT_DAYS, 5, Decimal("101")).close_time
        adapter.on_bar(_other(interval, close_time, Decimal("101")), _state())
        assert _kind(adapter.on_bar(_h4(_FLAT_DAYS, 5, Decimal("101")), _state())) == "none"


class TestStopHistory:
    """Only daily closes after the entry raise the stop, each with its own ATR."""

    def test_daily_closes_before_the_entry_never_raise_the_stop(self) -> None:
        """Closes fall 110 to 100, then cross at 101: the old levels (up to 104) do not apply.

        Previous close 100 is under SMA3 (102 + 101 + 100) / 3 = 101; 101 is over
        (101 + 100 + 101) / 3 = 100.67. Stop 95, so a next-day low of 99 holds.
        """
        falling = [Decimal(110 - i) for i in range(11)]
        adapter = _seeded_closes([Decimal("110")] * 20 + falling)
        entry = _close_day(adapter, 31, Decimal("101"))
        held = adapter.on_bar(_h4(32, 1, Decimal("100"), low=Decimal("99")), _state())
        assert (_summary(entry), held) == (
            (SignalKind.ENTRY, Decimal("101"), Decimal("95"), None),
            None,
        )

    def test_a_restart_raises_the_stop_from_each_daily_close_with_the_atr_up_to_it(self) -> None:
        """Entered at day 30's close 101; restarted after days 31-33 closed.

        Day 31 closes 102 (level 96), day 32 closes 103 (level 97), day 33 is
        wide (high 106, low 97, close 102; ATR rises to 2.318, level 95.05). The
        stop is 97: day 32's level uses the ATR up to day 32, 2. With day 33's
        bar in it the level would be 103 - 3 x 2.318 = 96.05. A low of 97 exits.
        """
        dailies = [_daily(d, _FLAT) for d in range(_FLAT_DAYS)]
        dailies += [
            _daily(30, Decimal("101")),
            _daily(31, Decimal("102")),
            _daily(32, Decimal("103")),
            _daily(33, Decimal("102"), high=Decimal("106"), low=Decimal("97")),
        ]
        entry_time = _h4(_FLAT_DAYS, 5, Decimal("101")).close_time
        adapter = DailyMaAdapter(sma_period=3)
        adapter.seed(dailies, OpenPosition(entry_time=entry_time, entry_price=Decimal("101")))
        signal = adapter.on_bar(_h4(34, 1, Decimal("100"), low=Decimal("97")), _state())
        assert _summary(signal) == (SignalKind.EXIT, Decimal("101"), Decimal("97"), PROTECTIVE_STOP)

    def test_a_restart_with_a_short_history_raises_from_the_closes_that_have_an_atr(
        self,
    ) -> None:
        """Entered at day 20's close; days 21-25 close 101 to 105; recorded stop 90.

        Day 21 is bar 22, too few for ATR(22), so it is skipped, not the end:
        day 25's level 105 - 6 = 99 still raises the stop, and a low of 99 exits.
        """
        closes = [_FLAT] * 21 + [Decimal(101 + i) for i in range(5)]
        entry_time = _DAY0 + timedelta(days=21)
        adapter = DailyMaAdapter(sma_period=3)
        adapter.seed(
            [_daily(d, c) for d, c in enumerate(closes)],
            OpenPosition(entry_time=entry_time, entry_price=_FLAT, last_stop=Decimal("90")),
        )
        signal = adapter.on_bar(_h4(26, 1, Decimal("104"), low=Decimal("99")), _state())
        assert _summary(signal) == (SignalKind.EXIT, _FLAT, Decimal("99"), PROTECTIVE_STOP)


class TestRestoreEdges:
    """Restoring keeps the stop of the position actually held."""

    def test_a_retried_exit_keeps_its_raised_stop_over_a_stale_record(self) -> None:
        """Raised to 95.9 by day 31's close; restored with last_stop 95: 95.9 stays."""
        adapter = _seeded()
        _entered(adapter)
        _close_day(adapter, _FLAT_DAYS + 1, Decimal("101.9"))
        entry_time = _h4(_FLAT_DAYS, 5, Decimal("101")).close_time
        adapter.restore_position(
            OpenPosition(entry_time=entry_time, entry_price=Decimal("101"), last_stop=Decimal("95"))
        )
        signal = adapter.on_bar(
            _h4(_FLAT_DAYS + 2, 1, Decimal("101"), low=Decimal("95.5")), _state()
        )
        assert _summary(signal) == (
            SignalKind.EXIT,
            Decimal("101"),
            Decimal("95.9"),
            PROTECTIVE_STOP,
        )

    def test_another_position_takes_its_own_stop(self) -> None:
        """Holding the day-30 entry (stop 95.9), restored to a day-31 entry at 100: stop 94."""
        adapter = _seeded()
        _entered(adapter)
        _close_day(adapter, _FLAT_DAYS + 1, Decimal("101.9"))
        other_time = _h4(_FLAT_DAYS + 1, 5, Decimal("101.9")).close_time
        adapter.restore_position(OpenPosition(entry_time=other_time, entry_price=_FLAT))
        low = Decimal("95")
        assert (
            _kind(adapter.on_bar(_h4(_FLAT_DAYS + 2, 1, Decimal("101"), low=low), _state()))
            == "none"
        )

    def test_after_an_unfilled_entry_a_close_below_is_not_an_exit(self) -> None:
        """Flat again after the unfilled entry: day 31 under the SMA emits nothing, day 32 enters.

        Day 31 at 100 is under SMA3 100.33; day 32 at 101 is over SMA3 100.67
        after 100 <= 100.33: a cross, stop 95.
        """
        adapter = _seeded()
        _entered(adapter)
        adapter.restore_position(None)
        below = _close_day(adapter, _FLAT_DAYS + 1, _FLAT)
        cross = _close_day(adapter, _FLAT_DAYS + 2, Decimal("101"))
        assert (below, _summary(cross)) == (
            None,
            (SignalKind.ENTRY, Decimal("101"), Decimal("95"), None),
        )

    def test_an_unfilled_entry_gives_no_reentry_right(self) -> None:
        """No position was closed, so day 31 at 102 over 101 (no cross) does not enter."""
        adapter = _seeded()
        _entered(adapter)
        adapter.restore_position(None)
        assert _kind(_close_day(adapter, _FLAT_DAYS + 1, Decimal("102"))) == "none"


class TestStopFloor:
    """An entry whose stop would be at or below zero is not taken."""

    @pytest.mark.parametrize(
        ("multiple", "expected"),
        [
            (Decimal("3"), None),
            (Decimal("3.25"), None),
            (Decimal("2.75"), (SignalKind.ENTRY, Decimal("6"), Decimal("0.5"), None)),
        ],
    )
    def test_the_stop_must_be_above_zero(
        self, multiple: Decimal, expected: tuple[object, ...] | None
    ) -> None:
        """Flat at 5, cross at 6, ATR 2: stops 0 and -0.5 are refused, 0.5 is taken."""
        adapter = _seeded_closes([Decimal("5")] * _FLAT_DAYS, stop_atr_mult=multiple)
        assert _summary(_close_day(adapter, _FLAT_DAYS, Decimal("6"))) == expected

    def test_a_negative_multiple_is_refused(self) -> None:
        """A negative distance would put the stop above the entry."""
        with pytest.raises(ValueError, match="stop_atr_mult must be positive"):
            DailyMaAdapter(stop_atr_mult=Decimal("-1"))

    def test_a_multiple_under_one_is_accepted(self) -> None:
        """Half an ATR is a valid, tight stop: flat at 100, cross at 101, stop 100."""
        signal = _entered(_seeded(stop_atr_mult=Decimal("0.5")))
        assert _summary(signal) == (SignalKind.ENTRY, Decimal("101"), Decimal("100"), None)


_STEP = st.sampled_from(
    [Decimal("-1"), Decimal("-0.5"), Decimal("0"), Decimal("0.5"), Decimal("1")]
)
_DIP = st.sampled_from([Decimal("0.5"), Decimal("3"), Decimal("7")])


class TestProperties:
    """Invariants over random daily paths with one intraday dip per day."""

    @given(steps=st.lists(st.tuples(_STEP, _DIP), min_size=1, max_size=25))
    def test_signals_alternate_and_the_stop_never_falls(
        self, steps: list[tuple[Decimal, Decimal]]
    ) -> None:
        """Entries and exits alternate, starting with an entry; each exit's stop >= its entry's."""
        adapter = _seeded()
        close = _FLAT
        signals: list[StrategySignal] = []
        for offset, (step, dip) in enumerate(steps):
            day = _FLAT_DAYS + offset
            midday = adapter.on_bar(_h4(day, 2, close, low=close - dip), _state())
            close += step
            decided = _close_day(adapter, day, close)
            signals += [s for s in (midday, decided) if s is not None]
        kinds = [s.kind for s in signals]
        expected = [SignalKind.ENTRY, SignalKind.EXIT] * len(kinds)
        lowered = [
            (entry.stop_price, exit_.stop_price)
            for entry, exit_ in zip(signals[::2], signals[1::2], strict=False)
            if exit_.stop_price < entry.stop_price
        ]
        assert (kinds, lowered) == (expected[: len(kinds)], [])
