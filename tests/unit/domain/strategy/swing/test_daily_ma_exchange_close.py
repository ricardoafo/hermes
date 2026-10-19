"""Regression tests for issue #1252: DailyMaAdapter decides on exchange-timed bars.

Invariant tested:
    Exchange bars close 1 ms before their boundary (a daily bar opening at
    00:00 closes at 23:59:59.999). The adapter takes its daily decision on
    the 4h bar that ends the UTC day under that convention exactly as it does
    when bars close on the boundary, so the batch swing-b1 §C2 rule fires on
    real data.

Before the fix the decision gate read ``close_time`` and required 00:00:00,
so on real bars the candidate never entered (Train run 39243463633).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import StrategySignal
from hermes.domain.strategy.swing.daily_ma import DailyMaAdapter
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_DAY0 = datetime(2021, 1, 1, tzinfo=UTC)
_FLAT_DAYS = 30
_FLAT = Decimal("100")
_ONE_MS = timedelta(milliseconds=1)


def _bar(interval: KlineInterval, open_time: datetime, close: Decimal, *, early: bool) -> Bar:
    span = timedelta(days=1) if interval is KlineInterval.D1 else timedelta(hours=4)
    return Bar(
        pair=_PAIR,
        interval=interval,
        open_time=open_time,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=Decimal("100"),
        close_time=open_time + span - (_ONE_MS if early else timedelta(0)),
    )


def _state() -> PortfolioState:
    return PortfolioState(
        peak_value=Decimal("5000"),
        current_value=Decimal("5000"),
        realized_slippage_cumulative=Decimal("0"),
        correlations={},
        dd_halt_required=False,
    )


def _kinds(closes: list[Decimal], *, early: bool) -> list[str]:
    """Seed 30 flat days, then close each later day and name what its last 4h bar emitted."""
    adapter = DailyMaAdapter(sma_period=3)
    adapter.seed(
        [
            _bar(KlineInterval.D1, _DAY0 + timedelta(days=d), _FLAT, early=early)
            for d in range(_FLAT_DAYS)
        ]
    )
    kinds: list[str] = []
    for offset, close in enumerate(closes):
        day = _DAY0 + timedelta(days=_FLAT_DAYS + offset)
        adapter.on_bar(_bar(KlineInterval.D1, day, close, early=early), _state())
        last_h4 = _bar(KlineInterval.H4, day + timedelta(hours=20), close, early=early)
        signal: StrategySignal | None = adapter.on_bar(last_h4, _state())
        kinds.append("none" if signal is None else signal.kind.value)
    return kinds


def test_a_cross_enters_on_exchange_timed_bars() -> None:
    """A close crossing above its SMA enters on the 4h bar closing at 23:59:59.999."""
    assert _kinds([Decimal("101")], early=True) == ["entry"]


def test_an_ma_break_exits_on_exchange_timed_bars() -> None:
    """A later close below the SMA exits on the bar that ends that UTC day."""
    assert _kinds([Decimal("101"), Decimal("100.5"), Decimal("99")], early=True) == [
        "entry",
        "none",
        "exit",
    ]


@given(
    st.lists(
        st.decimals(min_value=95, max_value=105, places=1, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=20,
    )
)
def test_decisions_do_not_depend_on_the_close_time_convention(closes: list[Decimal]) -> None:
    """Any path of daily closes yields the same decisions whichever convention bars close on."""
    assert _kinds(closes, early=True) == _kinds(closes, early=False)
