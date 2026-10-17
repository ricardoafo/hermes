"""Pin: serve runs the shadow set of rules, and the recorded inputs stay unread (#514, #835).

The Code Owner ruled on issue #514 that shadow runs what the backtest that
gated the strategy runs: RO-01 (correlation lock) on the 30-day correlations
of the 1D closes, as the portfolio backtest does (#822), and not RO-02 (spread
spike) or the live-phase recovery, single SA-9 and 30-trade Sharpe kills,
which the backtest never runs. These tests fail on purpose when that set
changes, so it is a deliberate change that updates them, never a side effect.
The import-linter contract ``risk-inputs-are-record-only`` in
``pyproject.toml`` keeps the recorded rows (#835) out of every decision.

Invariants under test:

- The portfolio snapshot carries no correlations; the executor supplies them.
- Serve's risk engine runs RO-01 and RO-03 to RO-08, never RO-02.
- Serve's market snapshot still sets the 1-hour median to the current spread.
- Serve's kill inputs leave the recovery, single SA-9 and 30-trade Sharpe
  kills neutral, whatever the portfolio did.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

from hermes.application.execution import shadow
from hermes.application.execution.shadow import shadow_risk_engine
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.spread_spike import SpreadSpike
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId


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


def test_serve_market_snapshot_median_is_still_the_current_spread() -> None:
    """``median_spread_1h`` is fed the current spread, not the recorded median."""
    source = inspect.getsource(shadow.ShadowExecutor._market_snapshot)
    assert "median_spread_1h=spread," in source
    assert "risk_inputs" not in inspect.getsource(shadow)


def test_serve_kill_inputs_leave_the_live_only_kills_neutral() -> None:
    """Recovery, single SA-9 and 30-trade Sharpe inputs are the backtest's neutral values."""
    source = inspect.getsource(shadow.ShadowExecutor._experiment_state)
    assert "days_since_equity_high=0.0" in source
    assert "sa9_halt=False" in source
    assert "rolling_30d_sharpe" not in source
