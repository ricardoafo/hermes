"""Pin: serve runs the shadow set of rules, and the recorded inputs stay unread (#514, #835).

The Code Owner ruled on issue #514 that shadow runs what the backtest that
gated the strategy runs: RO-01 (correlation lock) on the 30-day correlations
of the 1D closes, as the portfolio backtest does (#822), and not RO-02 (spread
spike) or the live-phase recovery, single SA-9 and 30-trade Sharpe kills,
which the backtest never runs. These tests fail on purpose when that set
changes, so it is a deliberate change that updates them, never a side effect.
Real money feeds these inputs through ``live_inputs`` (#1158,
``test_live_risk_inputs_reach_rules.py``); without it, shadow stays here.
The import-linter contract ``risk-inputs-are-record-only`` in
``pyproject.toml`` keeps the recorded rows (#835) out of every decision.

Invariants under test:

- The portfolio snapshot carries no correlations; the executor supplies them.
- Serve's risk engine runs RO-01 and RO-03 to RO-08, never RO-02.
- Serve's market snapshot still sets the 1-hour median to the current spread.
- Serve's kill inputs leave the recovery, single SA-9 and 30-trade Sharpe
  kills neutral, whatever the portfolio did.
"""

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from hermes.application.execution import shadow
from hermes.application.execution.shadow import shadow_risk_engine
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.spread_spike import SpreadSpike
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_STRATEGY = StrategyId("swing-donchian-4h")
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def test_the_snapshot_risk_state_carries_no_correlations() -> None:
    """``to_risk_state`` keeps ``correlations == {}``: the kernel takes them from the market."""
    snapshot = PortfolioSnapshot.seed(
        StrategyId("swing-donchian-4h"), Money(amount=Decimal(10000), currency=Currency.USDT)
    )
    assert snapshot.to_risk_state().correlations == {}


def test_serve_risk_engine_runs_ro01_and_never_ro02() -> None:
    """The engine's rule set is exactly RO-01 and RO-03 to RO-08 (#514, the shadow set)."""
    rules = shadow_risk_engine()._rules
    assert not any(isinstance(rule, SpreadSpike) for rule in rules)
    assert [type(rule).__name__ for rule in rules] == [
        "CorrelationLock",
        "MaxSlippage",
        "StaleBook",
        "DepegWarn",
        "DepegHalt",
        "MacroATRHalt",
        "PortfolioDrawdown",
    ]
    assert isinstance(rules[0], CorrelationLock)


def _stand_in(snapshot: PortfolioSnapshot) -> SimpleNamespace:
    """Return the executor state ``_market_snapshot`` and ``_experiment_state`` read."""
    return SimpleNamespace(
        _kernel=SimpleNamespace(tracker=SimpleNamespace(snapshot=snapshot)),
        _killed_by="",
        _live_inputs=None,
        _slippages=[],
        _btc_log_return_1d=lambda: None,
    )


def test_serve_market_snapshot_median_is_still_the_current_spread() -> None:
    """``median_spread_1h`` equals the book's current spread, never a recorded median."""
    snapshot = PortfolioSnapshot.seed(
        _STRATEGY, Money(amount=Decimal(10000), currency=Currency.USDT)
    )
    book = Orderbook(
        pair=_BTC,
        bids=(OrderbookLevel(price=Decimal("99"), quantity=Decimal("5")),),
        asks=(OrderbookLevel(price=Decimal("101"), quantity=Decimal("5")),),
        snapshot_time=_NOW,
    )
    bar = Bar(
        pair=_BTC,
        interval=KlineInterval.H4,
        open_time=_NOW - timedelta(hours=4),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
        close_time=_NOW,
    )

    market = shadow.ShadowExecutor._market_snapshot(_stand_in(snapshot), _BTC, book, bar, _NOW)  # type: ignore[arg-type]

    assert (market.current_spread, market.median_spread_1h) == (Decimal("0.02"), Decimal("0.02"))
    source = inspect.getsource(shadow)
    assert "hermes.application.risk_inputs" not in source
    assert "hermes.ports.risk_inputs" not in source


def test_serve_kill_inputs_leave_the_live_only_kills_neutral() -> None:
    """A 40% drawdown reaches the drawdown kill; recovery, SA-9 and Sharpe inputs stay neutral."""
    seeded = PortfolioSnapshot.seed(_STRATEGY, Money(amount=Decimal(10000), currency=Currency.USDT))
    snapshot = replace(seeded, cash=Decimal(6000))

    state = shadow.ShadowExecutor._experiment_state(_stand_in(snapshot), _NOW)  # type: ignore[arg-type]

    assert (
        state.max_drawdown,
        state.days_since_equity_high,
        state.sa9_halt,
        state.rolling_30d_sharpe,
    ) == (0.4, 0.0, False, None)
