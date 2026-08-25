"""Unit and matrix tests for RiskEngine.

Tests cover:
- HALT / WARN / OK outcome matrix — each action returned correctly.
- Worst-wins composition: HALT beats WARN beats OK.
- All decisions are preserved in RiskEngineResult.decisions.
- Empty rule list returns OK.
- Capital budget enforcement (SA-6): check_budget HALT / OK paths.
- Correlation lock refusal (RO-01 via CorrelationLock) wired through RiskEngine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.application.order_router.risk_engine import RiskEngine, RiskEngineResult
from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

# ---------------------------------------------------------------------------
# Stub rule implementations
# ---------------------------------------------------------------------------


@dataclass
class _FixedRule:
    """Stub risk rule that always returns a fixed RiskDecision."""

    action: RiskAction
    reason: str

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Return the fixed decision regardless of inputs."""
        return RiskDecision(action=self.action, reason=self.reason)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)

_EMPTY_PORTFOLIO = PortfolioState(
    peak_value=Decimal("100000"),
    current_value=Decimal("100000"),
    realized_slippage_cumulative=Decimal("0"),
)

_MARKET_SNAPSHOT = MarketSnapshot(
    pair=_BTC_USDT,
    current_spread=Decimal("0.001"),
    median_spread_1h=Decimal("0.001"),
    orderbook_age_seconds=0.5,
    orderbook_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    realized_slippage_last=Decimal("0.001"),
    stable_price=None,
    btc_log_return_1d=None,
    candidate_pair=_BTC_USDT,
)


# ---------------------------------------------------------------------------
# Empty rule list
# ---------------------------------------------------------------------------


class TestRiskEngineEmptyRules:
    """RiskEngine with no rules always returns OK."""

    def test_empty_rules_returns_ok(self) -> None:
        """An empty rule list must return RiskAction.OK."""
        engine = RiskEngine(rules=[])
        result = engine.evaluate(_EMPTY_PORTFOLIO, _MARKET_SNAPSHOT)
        assert isinstance(result, RiskEngineResult), (
            f"Expected RiskEngineResult, got {type(result)!r}."
        )
        assert result.worst.action == RiskAction.OK, (
            f"Expected RiskAction.OK for empty rule list, got {result.worst.action!r}."
        )

    def test_empty_rules_decisions_list_has_one_ok(self) -> None:
        """Empty rule list produces exactly one OK decision in the decisions list."""
        engine = RiskEngine(rules=[])
        result = engine.evaluate(_EMPTY_PORTFOLIO, _MARKET_SNAPSHOT)
        assert len(result.decisions) == 1, (
            f"Expected exactly 1 decision for empty rules, got {len(result.decisions)}."
        )
        assert result.decisions[0].action == RiskAction.OK, (
            f"Expected OK in decisions list, got {result.decisions[0].action!r}."
        )


# ---------------------------------------------------------------------------
# Single-rule matrix
# ---------------------------------------------------------------------------


class TestRiskEngineMatrixSingleRule:
    """HALT / WARN / OK outcome matrix — single rule wired."""

    @pytest.mark.parametrize(
        ("action", "expected_action"),
        [
            (RiskAction.HALT, RiskAction.HALT),
            (RiskAction.WARN, RiskAction.WARN),
            (RiskAction.OK, RiskAction.OK),
        ],
        ids=["halt", "warn", "ok"],
    )
    def test_single_rule_action_surfaced(
        self, action: RiskAction, expected_action: RiskAction
    ) -> None:
        """Single rule's action is surfaced verbatim as worst."""
        rule = _FixedRule(action=action, reason=f"stub {action.value}")
        engine = RiskEngine(rules=[rule])
        result = engine.evaluate(_EMPTY_PORTFOLIO, _MARKET_SNAPSHOT)
        assert result.worst.action == expected_action, (
            f"Expected worst={expected_action!r}, got {result.worst.action!r}. "
            f"Single rule action should propagate verbatim."
        )


# ---------------------------------------------------------------------------
# Composition worst-wins
# ---------------------------------------------------------------------------


class TestRiskEngineWorstWins:
    """Worst-wins composition: HALT beats WARN beats OK."""

    def test_halt_beats_warn(self) -> None:
        """When one rule is HALT and another is WARN, worst must be HALT."""
        engine = RiskEngine(
            rules=[
                _FixedRule(action=RiskAction.WARN, reason="warn stub"),
                _FixedRule(action=RiskAction.HALT, reason="halt stub"),
            ]
        )
        result = engine.evaluate(_EMPTY_PORTFOLIO, _MARKET_SNAPSHOT)
        assert result.worst.action == RiskAction.HALT, (
            f"Expected HALT to beat WARN, got {result.worst.action!r}."
        )

    def test_halt_beats_ok(self) -> None:
        """When one rule is HALT and another is OK, worst must be HALT."""
        engine = RiskEngine(
            rules=[
                _FixedRule(action=RiskAction.OK, reason="ok stub"),
                _FixedRule(action=RiskAction.HALT, reason="halt stub"),
            ]
        )
        result = engine.evaluate(_EMPTY_PORTFOLIO, _MARKET_SNAPSHOT)
        assert result.worst.action == RiskAction.HALT, (
            f"Expected HALT to beat OK, got {result.worst.action!r}."
        )

    def test_warn_beats_ok(self) -> None:
        """When one rule is WARN and another is OK, worst must be WARN."""
        engine = RiskEngine(
            rules=[
                _FixedRule(action=RiskAction.OK, reason="ok stub"),
                _FixedRule(action=RiskAction.WARN, reason="warn stub"),
            ]
        )
        result = engine.evaluate(_EMPTY_PORTFOLIO, _MARKET_SNAPSHOT)
        assert result.worst.action == RiskAction.WARN, (
            f"Expected WARN to beat OK, got {result.worst.action!r}."
        )

    def test_all_three_rules_halt_wins(self) -> None:
        """With OK + WARN + HALT rules, HALT must win."""
        engine = RiskEngine(
            rules=[
                _FixedRule(action=RiskAction.OK, reason="ok stub"),
                _FixedRule(action=RiskAction.WARN, reason="warn stub"),
                _FixedRule(action=RiskAction.HALT, reason="halt stub"),
            ]
        )
        result = engine.evaluate(_EMPTY_PORTFOLIO, _MARKET_SNAPSHOT)
        assert result.worst.action == RiskAction.HALT, (
            f"Expected HALT to win over all others, got {result.worst.action!r}."
        )

    def test_all_decisions_preserved(self) -> None:
        """All individual rule decisions are preserved in result.decisions."""
        engine = RiskEngine(
            rules=[
                _FixedRule(action=RiskAction.OK, reason="ok stub"),
                _FixedRule(action=RiskAction.WARN, reason="warn stub"),
                _FixedRule(action=RiskAction.HALT, reason="halt stub"),
            ]
        )
        result = engine.evaluate(_EMPTY_PORTFOLIO, _MARKET_SNAPSHOT)
        assert len(result.decisions) == 3, (
            f"Expected 3 decisions (one per rule), got {len(result.decisions)}. "
            f"All rule decisions must be preserved even when HALT is found."
        )
        actions = {d.action for d in result.decisions}
        expected = {RiskAction.OK, RiskAction.WARN, RiskAction.HALT}
        assert actions == expected, (
            f"Expected decisions to contain all three actions {expected!r}, got {actions!r}."
        )


# ---------------------------------------------------------------------------
# Capital budget enforcement (SA-6)
# ---------------------------------------------------------------------------


class TestRiskEngineCapitalBudget:
    """check_budget: SA-6 capital budget enforcement."""

    _STRATEGY = StrategyId("swing")
    _BUDGET = Money(amount=Decimal("5000"), currency=Currency.USDT)

    def test_budget_ok_when_within_limit(self) -> None:
        """check_budget returns OK when total does not exceed budget."""
        engine = RiskEngine(
            rules=[],
            capital_budget={self._STRATEGY: self._BUDGET},
        )
        decision = engine.check_budget(
            strategy_id=self._STRATEGY,
            requested_notional=Decimal("2000"),
            current_open_notional=Decimal("2000"),
        )
        assert decision.action == RiskAction.OK, (
            f"Expected OK when total (4000) < budget (5000), got {decision.action!r}."
        )

    def test_budget_halt_when_exceeded(self) -> None:
        """check_budget returns HALT when total would exceed budget."""
        engine = RiskEngine(
            rules=[],
            capital_budget={self._STRATEGY: self._BUDGET},
        )
        decision = engine.check_budget(
            strategy_id=self._STRATEGY,
            requested_notional=Decimal("3000"),
            current_open_notional=Decimal("3000"),
        )
        assert decision.action == RiskAction.HALT, (
            f"Expected HALT when total (6000) > budget (5000), got {decision.action!r}. "
            f"SA-6 budget enforcement must block over-allocation."
        )

    def test_budget_ok_for_unknown_strategy(self) -> None:
        """check_budget returns OK when no budget configured for the strategy."""
        engine = RiskEngine(rules=[], capital_budget={})
        decision = engine.check_budget(
            strategy_id=self._STRATEGY,
            requested_notional=Decimal("999999"),
            current_open_notional=Decimal("0"),
        )
        assert decision.action == RiskAction.OK, (
            f"Expected OK for strategy with no configured budget, got {decision.action!r}."
        )

    def test_halt_reason_mentions_sa6(self) -> None:
        """HALT reason from check_budget must mention SA-6 for traceability."""
        engine = RiskEngine(
            rules=[],
            capital_budget={self._STRATEGY: self._BUDGET},
        )
        decision = engine.check_budget(
            strategy_id=self._STRATEGY,
            requested_notional=Decimal("5001"),
            current_open_notional=Decimal("0"),
        )
        assert decision.action == RiskAction.HALT, (
            f"Expected HALT for over-budget request, got {decision.action!r}."
        )
        assert "SA-6" in decision.reason, (
            f"HALT reason must mention 'SA-6' for traceability. Got: {decision.reason!r}."
        )


# ---------------------------------------------------------------------------
# Correlation lock (RO-01) via CorrelationLock wired in RiskEngine
# ---------------------------------------------------------------------------


class TestRiskEngineCorrelationLock:
    """CorrelationLock (RO-01) wired into RiskEngine refuses correlated entries."""

    def test_correlated_pair_returns_halt(self) -> None:
        """RO-01 HALT when candidate pair correlation >= 0.80."""
        portfolio_with_correlation = PortfolioState(
            peak_value=Decimal("100000"),
            current_value=Decimal("100000"),
            realized_slippage_cumulative=Decimal("0"),
            correlations={(_BTC_USDT, _ETH_USDT): 0.85},
        )
        snapshot_with_candidate = MarketSnapshot(
            pair=_ETH_USDT,
            current_spread=Decimal("0.001"),
            median_spread_1h=Decimal("0.001"),
            orderbook_age_seconds=0.3,
            orderbook_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            realized_slippage_last=Decimal("0.001"),
            stable_price=None,
            btc_log_return_1d=None,
            candidate_pair=_ETH_USDT,
        )
        engine = RiskEngine(rules=[CorrelationLock()])
        result = engine.evaluate(portfolio_with_correlation, snapshot_with_candidate)
        assert result.worst.action == RiskAction.HALT, (
            f"Expected HALT from CorrelationLock for 0.85 correlation, "
            f"got {result.worst.action!r}. RO-01 must block entry."
        )

    def test_uncorrelated_pair_returns_ok(self) -> None:
        """RO-01 OK when candidate pair correlation < 0.80."""
        portfolio_low_corr = PortfolioState(
            peak_value=Decimal("100000"),
            current_value=Decimal("100000"),
            realized_slippage_cumulative=Decimal("0"),
            correlations={(_BTC_USDT, _ETH_USDT): 0.50},
        )
        snapshot_with_candidate = MarketSnapshot(
            pair=_ETH_USDT,
            current_spread=Decimal("0.001"),
            median_spread_1h=Decimal("0.001"),
            orderbook_age_seconds=0.3,
            orderbook_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            realized_slippage_last=Decimal("0.001"),
            stable_price=None,
            btc_log_return_1d=None,
            candidate_pair=_ETH_USDT,
        )
        engine = RiskEngine(rules=[CorrelationLock()])
        result = engine.evaluate(portfolio_low_corr, snapshot_with_candidate)
        assert result.worst.action == RiskAction.OK, (
            f"Expected OK from CorrelationLock for 0.50 correlation, got {result.worst.action!r}."
        )

    def test_correlation_at_threshold_returns_halt(self) -> None:
        """RO-01 HALT at exactly 0.80 (threshold is >=)."""
        portfolio_at_threshold = PortfolioState(
            peak_value=Decimal("100000"),
            current_value=Decimal("100000"),
            realized_slippage_cumulative=Decimal("0"),
            correlations={(_BTC_USDT, _ETH_USDT): 0.80},
        )
        snapshot_with_candidate = MarketSnapshot(
            pair=_ETH_USDT,
            current_spread=Decimal("0.001"),
            median_spread_1h=Decimal("0.001"),
            orderbook_age_seconds=0.3,
            orderbook_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            realized_slippage_last=Decimal("0.001"),
            stable_price=None,
            btc_log_return_1d=None,
            candidate_pair=_ETH_USDT,
        )
        engine = RiskEngine(rules=[CorrelationLock()])
        result = engine.evaluate(portfolio_at_threshold, snapshot_with_candidate)
        assert result.worst.action == RiskAction.HALT, (
            f"Expected HALT from CorrelationLock at exactly 0.80 correlation "
            f"(threshold is >=), got {result.worst.action!r}."
        )
