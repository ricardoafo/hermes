"""Tests for the pre-registration §6.2 execution book shared by backtest and serve.

Invariants protected here: the touch of the book sits at the pre-registered
execution price for each signal kind (§5.3, §6.2), and the backtest replay
uses the very same class serve imports (one implementation, ADR-011 D-01).
Under v2's close-based pricing every exit, the stop included, sells 5 bps under
the exit bar's close (pre-registration v2 §5.3, §6.2); the v1 default keeps the
stop-market price.

Reference: openspec/archive/353-fill-kernel-in-serve/change.md R-2;
backtest-driver-mvp1-phase2 change.md A-2.1; v2-strategy-config change.md R-1.
"""

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.backtest.replay import ExecutionModel as ReplayExecutionModel
from hermes.application.execution.prereg_book import ExecutionModel, ExitPricing
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_OPEN = datetime(2026, 10, 4, tzinfo=UTC)
_BAR = Bar(
    pair=_BTC,
    interval=KlineInterval.H4,
    open_time=_OPEN,
    open=Decimal("100"),
    high=Decimal("101"),
    low=Decimal("99"),
    close=Decimal("100"),
    volume=Decimal("1000"),
    close_time=close_time_of(_OPEN, KlineInterval.H4),
)


def _signal(kind: SignalKind, reason: str | None = None) -> StrategySignal:
    return StrategySignal(
        kind=kind,
        side=SignalSide.LONG,
        entry_price=Decimal("100") if kind is SignalKind.ENTRY else None,
        stop_price=Decimal("90"),
        atr_context=Decimal("2"),
        signal_time=_BAR.close_time,
        exit_reason=reason,
    )


def test_entry_book_asks_start_five_bps_above_the_close() -> None:
    """§6.2: entry at close x 1.0005."""
    book = ExecutionModel().book(_signal(SignalKind.ENTRY), _BAR)
    assert book is not None
    assert book.asks[0].price == Decimal("100.05")


def test_chandelier_exit_book_bids_start_fifteen_bps_below_the_stop() -> None:
    """§6.2: stop exit at stop x 0.9985."""
    book = ExecutionModel().book(_signal(SignalKind.EXIT, "chandelier"), _BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("89.865")


def test_regime_exit_book_bids_start_five_bps_below_the_close() -> None:
    """A non-stop exit sells at close x 0.9995."""
    book = ExecutionModel().book(_signal(SignalKind.EXIT, "regime_break"), _BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("99.95")


def test_backtest_replay_uses_the_same_execution_model_class() -> None:
    """One §6.2 implementation: the replay's name is this class."""
    assert ReplayExecutionModel is ExecutionModel


_GAP_BAR = Bar(
    pair=_BTC,
    interval=KlineInterval.H4,
    open_time=_OPEN,
    open=Decimal("88"),
    high=Decimal("89"),
    low=Decimal("85"),
    close=Decimal("86"),
    volume=Decimal("1000"),
    close_time=close_time_of(_OPEN, KlineInterval.H4),
)


def test_a_bar_opening_under_the_stop_sells_from_the_open() -> None:
    """§5.3 #785: a resting stop-market under a gap fills at the open 88, x 0.9985, not at 90."""
    book = ExecutionModel().book(_signal(SignalKind.EXIT, "chandelier"), _GAP_BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("87.868")


def test_a_gap_stop_exit_references_the_open() -> None:
    """The M-02 reference of a gap stop exit is the open the stop filled at (#785)."""
    reference = ExecutionModel().reference(_signal(SignalKind.EXIT, "chandelier"), _GAP_BAR)
    assert reference == (Decimal("88"), Decimal("15"))


def test_a_gap_under_the_stop_leaves_a_regime_exit_at_the_close() -> None:
    """Only a stop exit fills at the open: a regime exit on the gap bar sells at close x 0.9995."""
    book = ExecutionModel().book(_signal(SignalKind.EXIT, "regime_break"), _GAP_BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("85.957")


_V2 = ExecutionModel(exit_pricing=ExitPricing.BAR_CLOSE)

_CRASH_BAR = Bar(
    pair=_BTC,
    interval=KlineInterval.H4,
    open_time=_OPEN,
    open=Decimal("100"),
    high=Decimal("101"),
    low=Decimal("80"),
    close=Decimal("82"),
    volume=Decimal("1000"),
    close_time=close_time_of(_OPEN, KlineInterval.H4),
)
"""Opens above the 90 stop, trades through it and closes 8 under it."""


def test_the_default_execution_model_keeps_the_v1_stop_order_pricing() -> None:
    """v1 is unchanged: a model built without a pricing prices stops as a stop-market."""
    assert ExecutionModel().exit_pricing is ExitPricing.STOP_ORDER


def test_v1_prices_a_stop_through_bar_at_the_stop_not_the_close() -> None:
    """v1 §6.2 on the crash bar: the stop 90 x 0.9985, whatever the bar closes at."""
    book = ExecutionModel().book(_signal(SignalKind.EXIT, "chandelier"), _CRASH_BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("89.865")


def test_v2_sells_a_stop_exit_five_bps_under_the_bar_close() -> None:
    """v2 §5.3/§6.2: the bar that reached the 90 stop and closed at 82 sells at 82 x 0.9995."""
    book = _V2.book(_signal(SignalKind.EXIT, "chandelier"), _CRASH_BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("81.959")


def test_v2_references_a_stop_exit_at_the_bar_close_with_the_market_half_spread() -> None:
    """The v2 M-02 and drift reference of a stop exit is the close, 5 bps away."""
    assert _V2.reference(_signal(SignalKind.EXIT, "chandelier"), _CRASH_BAR) == (
        Decimal("82"),
        Decimal("5"),
    )


def test_v2_sells_a_gap_stop_exit_at_the_close_not_the_open() -> None:
    """v2 never uses the open: the gap bar opening at 88 and closing at 86 sells at 86 x 0.9995."""
    book = _V2.book(_signal(SignalKind.EXIT, "chandelier"), _GAP_BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("85.957")


def test_v2_sells_a_time_stop_five_bps_under_the_bar_close() -> None:
    """v2 §6.2 'every exit': the time stop on the crash bar sells at 82 x 0.9995."""
    book = _V2.book(_signal(SignalKind.EXIT, "time_stop"), _CRASH_BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("81.959")


def test_v2_sells_a_regime_break_five_bps_under_the_bar_close() -> None:
    """v2 §6.2 'every exit': the regime break on the crash bar sells at 82 x 0.9995."""
    book = _V2.book(_signal(SignalKind.EXIT, "regime_break"), _CRASH_BAR)
    assert book is not None
    assert book.bids[0].price == Decimal("81.959")


def test_v2_leaves_the_entry_five_bps_over_the_close() -> None:
    """v2 §6.2 entry is v1's: the asks start at close x 1.0005."""
    book = _V2.book(_signal(SignalKind.ENTRY), _BAR)
    assert book is not None
    assert book.asks[0].price == Decimal("100.05")


def test_exit_pricing_values_are_the_yaml_spellings() -> None:
    """The config spells the two pricings ``stop_order`` and ``bar_close``."""
    assert [p.value for p in ExitPricing] == ["stop_order", "bar_close"]


_PRICES = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("200000"), places=2, allow_nan=False
)
_EXIT_REASONS = st.sampled_from(["chandelier", "time_stop", "regime_break"])


@st.composite
def _bars(draw: st.DrawFn) -> Bar:
    """Draw a valid 4h bar: open and close inside [low, high]."""
    low = draw(_PRICES)
    high = low + draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("50000"), places=2))
    inside = st.decimals(min_value=low, max_value=high, places=2)
    return Bar(
        pair=_BTC,
        interval=KlineInterval.H4,
        open_time=_OPEN,
        open=draw(inside),
        high=high,
        low=low,
        close=draw(inside),
        volume=Decimal("1000"),
        close_time=close_time_of(_OPEN, KlineInterval.H4),
    )


def _exit_signal(reason: str, stop: Decimal) -> StrategySignal:
    return StrategySignal(
        kind=SignalKind.EXIT,
        side=SignalSide.LONG,
        entry_price=None,
        stop_price=stop,
        atr_context=Decimal("2"),
        signal_time=_BAR.close_time,
        exit_reason=reason,
    )


@given(bar=_bars(), reason=_EXIT_REASONS, stop=_PRICES)
def test_every_v2_exit_touch_is_the_bar_close_less_five_bps(
    bar: Bar, reason: str, stop: Decimal
) -> None:
    """Property: for any exit reason, stop, open or range, the v2 bid touch is close x 0.9995."""
    book = _V2.book(_exit_signal(reason, stop), bar)
    assert book is not None
    assert book.bids[0].price == bar.close * Decimal("0.9995")


@given(bar=_bars(), stop=_PRICES)
def test_every_v1_stop_exit_references_the_lower_of_stop_and_open(bar: Bar, stop: Decimal) -> None:
    """Property: v1 keeps PR #955's stop price, min(stop, open) with the 15 bps half-spread."""
    reference = ExecutionModel().reference(_exit_signal("chandelier", stop), bar)
    assert reference == (min(stop, bar.open), Decimal("15"))
