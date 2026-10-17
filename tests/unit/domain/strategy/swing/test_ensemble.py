"""Unit tests for BreakoutEnsembleAdapter, batch swing-b1 candidate C1 (issue #1063).

Rules under test (research batch swing-b1 §C1, card swing-b1-ensemble):

- Entry, on a 4h bar close: at least ``agree_min`` of the lookbacks
  {20, 55, ``longest_lookback``} see the close above the highest high of the
  previous L bars, and the last daily close is above its EMA(200).
- Exits are v2's, with the chandelier ATR multiple ``chandelier_atr_mult``.

The fixture places two old highs so the count of agreeing lookbacks is known by
hand: flat bars at 100 (high 100), one bar 30 back with high 110 (inside the 55
and longest windows only) and one bar 80 back with high 120 (inside the longest
window only). A signal close of 105 breaks 1 lookback, 115 breaks 2, 125 all 3.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.strategy_protocol.strategy_factory import (
    BREAKOUT_ENSEMBLE,
    known_signals,
    strategy_kind,
)
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.domain.strategy.swing.adapter import EntryCheck
from hermes.domain.strategy.swing.engines.chandelier import ChandelierEngine
from hermes.domain.strategy.swing.ensemble import BreakoutEnsembleAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config import StrategiesConfig
from hermes.infrastructure.config.loader import load_yaml_model

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_DAYS = 210
_D1_START = datetime(2021, 1, 1, tzinfo=UTC)
_H4_START = _D1_START + timedelta(days=_DAYS)
_HISTORY = 130
_HIGH_30_BACK = Decimal("110")
_HIGH_80_BACK = Decimal("120")


def _bar(index: int, close: Decimal, interval: KlineInterval, high: Decimal | None = None) -> Bar:
    hours = 4 if interval is KlineInterval.H4 else 24
    start = _H4_START if interval is KlineInterval.H4 else _D1_START
    open_time = start + timedelta(hours=hours * index)
    return Bar(
        pair=_PAIR,
        interval=interval,
        open_time=open_time,
        open=close,
        high=close if high is None else max(high, close),
        low=close * Decimal("0.99"),
        close=close,
        volume=Decimal("100"),
        close_time=open_time + timedelta(hours=hours),
    )


def _daily(*, rising: bool) -> list[Bar]:
    step = Decimal("0.1") if rising else Decimal("-0.1")
    return [_bar(i, Decimal("100") + step * i, KlineInterval.D1) for i in range(_DAYS)]


def _history(highs_back: dict[int, Decimal]) -> list[Bar]:
    """``_HISTORY`` flat 4h bars at 100; ``highs_back[k]`` is the high of the bar k back."""
    return [
        _bar(i, Decimal("100"), KlineInterval.H4, highs_back.get(_HISTORY - i))
        for i in range(_HISTORY)
    ]


_TWO_OLD_HIGHS = {30: _HIGH_30_BACK, 80: _HIGH_80_BACK}


def _state() -> PortfolioState:
    return PortfolioState(
        peak_value=Decimal("5000"),
        current_value=Decimal("5000"),
        realized_slippage_cumulative=Decimal("0"),
        correlations={},
        dd_halt_required=False,
    )


def _signal(
    adapter: BreakoutEnsembleAdapter,
    close: Decimal,
    *,
    highs_back: dict[int, Decimal] | None = None,
    rising: bool = True,
) -> StrategySignal | None:
    history = _history(_TWO_OLD_HIGHS if highs_back is None else highs_back)
    adapter.seed([*_daily(rising=rising), *history])
    return adapter.on_bar(_bar(_HISTORY, close, KlineInterval.H4), _state())


def _enters(
    adapter: BreakoutEnsembleAdapter,
    close: Decimal,
    *,
    highs_back: dict[int, Decimal] | None = None,
    rising: bool = True,
) -> bool:
    signal = _signal(adapter, close, highs_back=highs_back, rising=rising)
    return signal is not None and signal.kind is SignalKind.ENTRY


class TestAgreement:
    """``agree_min`` of the three lookbacks must break out."""

    # SEV-1: the candidate must trade its signed entry rule (batch swing-b1 §C1)
    def test_two_agreeing_lookbacks_enter_at_the_base(self) -> None:
        """Close 115 is above every high in the 20 and 55 windows, below 120: 2 agree."""
        assert _enters(BreakoutEnsembleAdapter(), Decimal("115"))

    # SEV-1: one breakout alone must not enter at the base agree_min of 2
    def test_one_breakout_alone_does_not_enter_at_the_base(self) -> None:
        """Close 105 is below the 110 high 30 bars back: only the 20-bar lookback breaks."""
        assert not _enters(BreakoutEnsembleAdapter(), Decimal("105"))

    def test_agree_min_one_enters_on_a_single_breakout(self) -> None:
        """The lower neighbour: the 20-bar breakout alone is enough."""
        assert _enters(BreakoutEnsembleAdapter(agree_min=1), Decimal("105"))

    def test_agree_min_three_needs_every_lookback(self) -> None:
        """The upper neighbour: close 115 breaks 2 of 3, so no entry."""
        assert not _enters(BreakoutEnsembleAdapter(agree_min=3), Decimal("115"))

    def test_agree_min_three_enters_when_every_lookback_breaks(self) -> None:
        """Close 125 is above the 120 high 80 bars back as well: 3 agree."""
        assert _enters(BreakoutEnsembleAdapter(agree_min=3), Decimal("125"))

    def test_a_close_equal_to_the_old_high_is_not_a_breakout(self) -> None:
        """The close must be strictly above the highest high: 110 does not break the 55 window."""
        assert not _enters(BreakoutEnsembleAdapter(), _HIGH_30_BACK)


class TestLongestLookback:
    """The third lookback is ``longest_lookback`` 4h bars."""

    def test_a_high_outside_the_longest_window_does_not_count(self) -> None:
        """A 120 high 100 bars back is outside a 90-bar window: close 115 breaks all 3."""
        highs = {30: _HIGH_30_BACK, 100: _HIGH_80_BACK}
        adapter = BreakoutEnsembleAdapter(agree_min=3, longest_lookback=90)
        assert _enters(adapter, Decimal("115"), highs_back=highs)

    def test_the_same_high_inside_the_longest_window_blocks(self) -> None:
        """At the base 120 bars, that high is inside the window: close 115 breaks only 2."""
        highs = {30: _HIGH_30_BACK, 100: _HIGH_80_BACK}
        adapter = BreakoutEnsembleAdapter(agree_min=3, longest_lookback=120)
        assert not _enters(adapter, Decimal("115"), highs_back=highs)


class TestRegime:
    """The last daily close must be above its EMA(200), as in v2."""

    # SEV-1: the regime filter must hold for the candidate as for v2 (batch §C1)
    def test_a_falling_daily_trend_blocks_every_breakout(self) -> None:
        """All three lookbacks break, but the daily close is below its EMA(200)."""
        assert not _enters(BreakoutEnsembleAdapter(agree_min=1), Decimal("125"), rising=False)


class TestEntryRecord:
    """The journal's yes/no record names the C1 conditions."""

    def test_the_record_shows_the_missing_agreement(self) -> None:
        """Close 105 at the base: agreement fails, there is no ADX filter, the regime holds."""
        adapter = BreakoutEnsembleAdapter()
        _signal(adapter, Decimal("105"))
        assert adapter.last_entry_check == EntryCheck(breakout=False, momentum=True, regime=True)


class TestExits:
    """C1 exits are v2's, with its own chandelier ATR multiple."""

    @pytest.mark.parametrize("multiple", [Decimal("2.5"), Decimal("3.0"), Decimal("3.5")])
    def test_the_entry_stop_uses_the_candidate_multiple(self, multiple: Decimal) -> None:
        """The entry's stop is highest_high(22) - multiple x ATR(22) of the bars closed by then."""
        adapter = BreakoutEnsembleAdapter(chandelier_atr_mult=multiple)
        signal = _signal(adapter, Decimal("115"))
        bars = [*_history(_TWO_OLD_HIGHS), _bar(_HISTORY, Decimal("115"), KlineInterval.H4)]
        expected = ChandelierEngine.trailing_stop(bars, period=22, atr_mult=multiple)
        seen = None if signal is None else (signal.kind, signal.stop_price)
        assert seen == (SignalKind.ENTRY, expected)


class TestParameters:
    """The kind's parameters are the batch's, at the signed base values."""

    def test_the_kind_is_registered(self) -> None:
        """``signal: breakout_ensemble`` builds this adapter."""
        assert (BREAKOUT_ENSEMBLE in known_signals(), BREAKOUT_ENSEMBLE) == (
            True,
            "breakout_ensemble",
        )

    def test_the_roster_entry_builds_this_kind(self) -> None:
        """``swing-b1-ensemble`` in the shipped roster names ``signal: breakout_ensemble``."""
        roster = load_yaml_model(
            Path(__file__).resolve().parents[5] / "config" / "strategies.yaml",
            StrategiesConfig,
            env_section="STRATEGIES",
            env={},
        )
        kinds = [
            s.strategy_kind().signal for s in roster.strategies if s.name == "swing-b1-ensemble"
        ]
        assert kinds == [BREAKOUT_ENSEMBLE]

    def test_the_defaults_are_the_signed_base_values(self) -> None:
        """agree_min 2, chandelier_atr_mult 3.0, longest_lookback 120 (batch swing-b1 §C1)."""
        assert strategy_kind(BREAKOUT_ENSEMBLE).default_parameters() == {
            "agree_min": 2,
            "chandelier_atr_mult": Decimal("3.0"),
            "longest_lookback": 120,
        }

    def test_every_neighbour_builds(self) -> None:
        """Each neighbour value of the batch builds an adapter of this kind."""
        kind = strategy_kind(BREAKOUT_ENSEMBLE)
        neighbours = [
            {"agree_min": 1},
            {"agree_min": 3},
            {"chandelier_atr_mult": Decimal("2.5")},
            {"chandelier_atr_mult": Decimal("3.5")},
            {"longest_lookback": 90},
            {"longest_lookback": 180},
        ]
        built = [type(kind.create(n)).__name__ for n in neighbours]
        assert built == ["BreakoutEnsembleAdapter"] * len(neighbours)

    @pytest.mark.parametrize("agree_min", [0, 4])
    def test_agree_min_outside_one_to_three_is_refused(self, agree_min: int) -> None:
        """There are three lookbacks, so 1 to 3 of them can agree."""
        with pytest.raises(ValueError, match="agree_min"):
            BreakoutEnsembleAdapter(agree_min=agree_min)

    @pytest.mark.parametrize("longest_lookback", [20, 55])
    def test_a_longest_lookback_not_past_55_is_refused(self, longest_lookback: int) -> None:
        """The longest lookback must be longer than the fixed 55-bar one."""
        with pytest.raises(ValueError, match="longest_lookback"):
            BreakoutEnsembleAdapter(longest_lookback=longest_lookback)

    def test_a_longest_lookback_of_56_is_accepted(self) -> None:
        """56 is the shortest longest lookback, one past the fixed 55."""
        assert type(BreakoutEnsembleAdapter(longest_lookback=56)) is BreakoutEnsembleAdapter


_PRICE = st.integers(min_value=101, max_value=130).map(Decimal)


class TestProperties:
    """Invariants over every placement of two old highs and any close."""

    @given(close=_PRICE, near=_PRICE, far=_PRICE)
    def test_a_stricter_agree_min_never_enters_where_a_looser_one_does_not(
        self, close: Decimal, near: Decimal, far: Decimal
    ) -> None:
        """Entering at agree_min k implies entering at k - 1: the rule is a count threshold."""
        highs = {30: near, 80: far}
        entered = [
            _enters(BreakoutEnsembleAdapter(agree_min=k), close, highs_back=highs)
            for k in (1, 2, 3)
        ]
        assert entered == sorted(entered, reverse=True)

    @given(close=_PRICE, near=_PRICE, far=_PRICE)
    def test_the_agreement_count_matches_the_three_windows(
        self, close: Decimal, near: Decimal, far: Decimal
    ) -> None:
        """At agree_min 3 the adapter enters exactly when the close beats both old highs.

        Every window holds the flat 100 highs; the 55 and 120 windows hold the
        30-back high, and only the 120 window holds the 80-back one.
        """
        highs = {30: near, 80: far}
        adapter = BreakoutEnsembleAdapter(agree_min=3)
        assert _enters(adapter, close, highs_back=highs) == (close > near and close > far)
