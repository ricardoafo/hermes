"""Pin: the recorded RO-01 / RO-02 inputs are read by no rule (#835).

Issue #835 records the 1-hour median spread and the 30-day pair correlations
only. Wiring RO-01 (correlation lock) or RO-02 (spread spike) changes trading
decisions and waits for the Code Owner's ruling (issue #514). These tests
fail on purpose when that wiring lands, so it is a deliberate change that
updates them, never a side effect. The import-linter contract
``risk-inputs-are-record-only`` in ``pyproject.toml`` pins the same line at
the module level.

Invariants under test:

- The portfolio state handed to the risk rules carries no correlations.
- Serve's risk engine runs neither RO-01 nor RO-02.
- Serve's market snapshot still sets the 1-hour median to the current spread.
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


def test_risk_state_still_passes_no_correlations() -> None:
    """``to_risk_state`` keeps ``correlations == {}``: RO-01 has no input yet."""
    snapshot = PortfolioSnapshot.seed(
        StrategyId("swing-donchian-4h"), Money(amount=Decimal("10000"), currency=Currency.USDT)
    )
    assert snapshot.to_risk_state().correlations == {}


def test_serve_risk_engine_runs_neither_ro01_nor_ro02() -> None:
    """The engine's rule set is exactly RO-03 to RO-08."""
    rules = shadow_risk_engine()._rules
    assert not any(isinstance(rule, CorrelationLock | SpreadSpike) for rule in rules)
    assert [type(rule).__name__ for rule in rules] == [
        "MaxSlippage",
        "StaleBook",
        "DepegWarn",
        "DepegHalt",
        "MacroATRHalt",
        "PortfolioDrawdown",
    ]


def test_serve_market_snapshot_median_is_still_the_current_spread() -> None:
    """``median_spread_1h`` is fed the current spread, not the recorded median."""
    source = inspect.getsource(shadow.ShadowExecutor._market_snapshot)
    assert "median_spread_1h=spread," in source
    assert "risk_inputs" not in inspect.getsource(shadow)
