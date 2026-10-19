"""Tests for risk rule composition — worst-outcome-wins logic.

When multiple rules are evaluated, the result with the highest severity
(HALT > WARN > OK) wins. These tests verify the composition invariant
across all 8 rules.

Reference: spec-8-risk-overlay change.md § Decisions D-1; R-3.
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.depeg_halt import DepegHalt
from hermes.domain.risk.depeg_warn import DepegWarn
from hermes.domain.risk.macro_atr_halt import MacroATRHalt
from hermes.domain.risk.max_slippage import MaxSlippage
from hermes.domain.risk.portfolio_drawdown import PortfolioDrawdown
from hermes.domain.risk.spread_spike import SpreadSpike
from hermes.domain.risk.stale_book import StaleBook
from hermes.ports.risk_overlay import RiskAction, RiskDecision, RiskOverlayPort
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state


def worst_outcome(decisions: list[RiskDecision]) -> RiskDecision:
    """Compose multiple decisions by returning the highest-severity outcome.

    Args:
        decisions: Non-empty list of rule decisions.

    Returns:
        The decision with the highest severity (HALT > WARN > OK).
        When multiple decisions share the highest severity, the first
        one encountered wins (deterministic, matches iteration order).

    """
    result = decisions[0]
    for d in decisions[1:]:
        if d.action.is_worse_than(result.action):
            result = d
    return result


# ────────────────────────────────────────────────────────────────
# All-OK baseline
# ────────────────────────────────────────────────────────────────


class TestCompositionAllOK:
    """When all rules return OK the composed result is OK."""

    def test_all_rules_ok_composes_to_ok(self) -> None:
        """All 8 rules returning OK must compose to a single OK decision."""
        rules: list[RiskOverlayPort] = [
            CorrelationLock(),
            SpreadSpike(),
            MaxSlippage(),
            StaleBook(),
            DepegWarn(),
            DepegHalt(),
            MacroATRHalt(),
            PortfolioDrawdown(),
        ]
        state = make_portfolio_state()
        # Default snapshot: all signals within safe range
        snapshot = make_market_snapshot(btc_log_return_1d=0.0)
        decisions = [r.evaluate(portfolio_state=state, market_snapshot=snapshot) for r in rules]
        composed = worst_outcome(decisions)
        assert composed.action == RiskAction.OK, (
            f"All rules OK must compose to OK, got {composed.action}: {composed.reason}"
        )


# ────────────────────────────────────────────────────────────────
# HALT wins over WARN and OK
# ────────────────────────────────────────────────────────────────


class TestCompositionHaltWinsOverWarn:
    """HALT beats WARN in worst-outcome composition."""

    def test_halt_plus_warn_composes_to_halt(self) -> None:
        """When one rule HALTs and another WARNs, composition must return HALT."""
        halt_decision = RiskDecision(action=RiskAction.HALT, reason="rule A halted")
        warn_decision = RiskDecision(action=RiskAction.WARN, reason="rule B warned")
        composed = worst_outcome([warn_decision, halt_decision])
        assert composed.action == RiskAction.HALT, (
            f"HALT + WARN must compose to HALT, got {composed.action}"
        )


class TestCompositionHaltWinsOverOK:
    """HALT beats OK in worst-outcome composition."""

    def test_halt_plus_ok_composes_to_halt(self) -> None:
        """When one rule HALTs and others return OK, composition must return HALT."""
        halt = RiskDecision(action=RiskAction.HALT, reason="stale book")
        ok1 = RiskDecision(action=RiskAction.OK, reason="correlation OK")
        ok2 = RiskDecision(action=RiskAction.OK, reason="slippage OK")
        composed = worst_outcome([ok1, halt, ok2])
        assert composed.action == RiskAction.HALT, (
            f"HALT among OKs must compose to HALT, got {composed.action}"
        )


class TestCompositionWarnWinsOverOK:
    """WARN beats OK in worst-outcome composition."""

    def test_warn_plus_ok_composes_to_warn(self) -> None:
        """When one rule WARNs and others return OK, composition must return WARN."""
        warn = RiskDecision(action=RiskAction.WARN, reason="depeg soft warning")
        ok1 = RiskDecision(action=RiskAction.OK, reason="spread OK")
        ok2 = RiskDecision(action=RiskAction.OK, reason="book fresh")
        composed = worst_outcome([ok1, ok2, warn])
        assert composed.action == RiskAction.WARN, (
            f"WARN among OKs must compose to WARN, got {composed.action}"
        )


# ────────────────────────────────────────────────────────────────
# Real-rule integration: stale book + depeg warn
# ────────────────────────────────────────────────────────────────


class TestCompositionRealRules:
    """Compose real rule instances to verify end-to-end worst-outcome behavior."""

    def test_stale_book_halt_wins_over_depeg_warn(self) -> None:
        """StaleBook HALT must win over DepegWarn WARN in composition."""
        rules: list[RiskOverlayPort] = [DepegWarn(), StaleBook()]
        state = make_portfolio_state()
        # Stale book: age > 2s; depeg warn: deviation ≥ 1.5% but < 3%
        snapshot = make_market_snapshot(
            orderbook_age_seconds=5.0,
            stable_price=Decimal("0.98"),  # 2% below peg — WARN
        )
        decisions = [r.evaluate(portfolio_state=state, market_snapshot=snapshot) for r in rules]
        assert any(d.action == RiskAction.HALT for d in decisions), (
            "StaleBook must return HALT for age=5.0s"
        )
        assert any(d.action == RiskAction.WARN for d in decisions), (
            "DepegWarn must return WARN for 2% depeg"
        )
        composed = worst_outcome(decisions)
        assert composed.action == RiskAction.HALT, (
            f"StaleBook HALT must beat DepegWarn WARN, got {composed.action}: {composed.reason}"
        )

    def test_all_ok_snapshot_returns_ok(self) -> None:
        """All 8 real rules with a safe snapshot must compose to OK."""
        rules: list[RiskOverlayPort] = [
            CorrelationLock(),
            SpreadSpike(),
            MaxSlippage(),
            StaleBook(),
            DepegWarn(),
            DepegHalt(),
            MacroATRHalt(),
            PortfolioDrawdown(),
        ]
        state = make_portfolio_state()
        snapshot = make_market_snapshot(
            current_spread=Decimal("0.001"),
            median_spread_1h=Decimal("0.001"),
            orderbook_age_seconds=0.5,
            realized_slippage_last=Decimal("0.001"),
            stable_price=None,
            btc_log_return_1d=0.0,
            candidate_pair=None,
        )
        decisions = [r.evaluate(portfolio_state=state, market_snapshot=snapshot) for r in rules]
        composed = worst_outcome(decisions)
        assert composed.action == RiskAction.OK, (
            f"Safe snapshot must compose to OK across all 8 rules, got {composed.action}"
        )


# ────────────────────────────────────────────────────────────────
# RiskAction severity ordering
# ────────────────────────────────────────────────────────────────


class TestRiskActionSeverityOrdering:
    """is_worse_than() respects HALT > WARN > OK ordering."""

    def test_halt_is_worse_than_warn(self) -> None:
        """HALT.is_worse_than(WARN) must be True."""
        assert RiskAction.HALT.is_worse_than(RiskAction.WARN), "HALT must be worse than WARN"

    def test_halt_is_worse_than_ok(self) -> None:
        """HALT.is_worse_than(OK) must be True."""
        assert RiskAction.HALT.is_worse_than(RiskAction.OK), "HALT must be worse than OK"

    def test_warn_is_worse_than_ok(self) -> None:
        """WARN.is_worse_than(OK) must be True."""
        assert RiskAction.WARN.is_worse_than(RiskAction.OK), "WARN must be worse than OK"

    def test_ok_is_not_worse_than_warn(self) -> None:
        """OK.is_worse_than(WARN) must be False."""
        assert not RiskAction.OK.is_worse_than(RiskAction.WARN), "OK must not be worse than WARN"

    def test_ok_is_not_worse_than_halt(self) -> None:
        """OK.is_worse_than(HALT) must be False."""
        assert not RiskAction.OK.is_worse_than(RiskAction.HALT), "OK must not be worse than HALT"

    def test_halt_is_not_worse_than_halt(self) -> None:
        """HALT.is_worse_than(HALT) must be False — same severity."""
        assert not RiskAction.HALT.is_worse_than(RiskAction.HALT), (
            "HALT must not be worse than itself"
        )

    def test_severity_ranks_ok_warn_halt(self) -> None:
        """The one ordinal is OK 0, WARN 1, HALT 2 (#1179: the engine sorts by it)."""
        assert [a.severity for a in (RiskAction.OK, RiskAction.WARN, RiskAction.HALT)] == [0, 1, 2]

    def test_is_worse_than_agrees_with_severity_for_every_pair(self) -> None:
        """is_worse_than is exactly 'higher severity', for all nine ordered pairs."""
        disagreements = [
            (a, b)
            for a in RiskAction
            for b in RiskAction
            if a.is_worse_than(b) != (a.severity > b.severity)
        ]
        assert disagreements == []
