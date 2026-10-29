"""Tests for RO-05 DepegWarn rule.

Property tests verify the soft-threshold boundary at 1.5% deviation (pre-reg §5.5):
  - deviation < 1.5% → OK
  - deviation ≥ 1.5% → WARN
  - stable_price is None → OK (not a stablecoin pair)

Reference: SA-9 §5.5 RO-05; spec-8-risk-overlay change.md R-2.
"""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.depeg_warn import _WARN_THRESHOLD, DepegWarn
from hermes.ports.risk_overlay import RiskAction
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = DepegWarn()
_PEG = Decimal("1.00")


class TestDepegWarnNoStablePrice:
    """Rule skips evaluation when stable_price is None."""

    def test_none_stable_price_returns_ok(self) -> None:
        """stable_price=None means non-stablecoin pair — rule must return OK."""
        snapshot = make_market_snapshot(stable_price=None)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.OK, (
            f"stable_price=None must return OK (not a stablecoin), got {decision.action}"
        )


class TestDepegWarnBelowThreshold:
    """Price deviation < 1.5% returns OK."""

    @given(
        deviation=st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("0.0149"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_below_threshold_returns_ok(self, deviation: Decimal) -> None:
        """Stablecoin deviation < 1.5% must return OK (no warning yet)."""
        price = _PEG + deviation  # above peg; deviation = (price - 1) / 1
        snapshot = make_market_snapshot(stable_price=price)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        actual_dev = abs(price - _PEG) / _PEG
        assert decision.action == RiskAction.OK, (
            f"price={price}, deviation={actual_dev:.4%} < {_WARN_THRESHOLD:.4%}:"
            f" expected OK, got {decision.action} — {decision.reason}"
        )


class TestDepegWarnAtOrAboveThreshold:
    """Price deviation ≥ 1.5% returns WARN."""

    @given(
        deviation=st.decimals(
            min_value=Decimal("0.015"),
            max_value=Decimal("0.029"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_at_or_above_threshold_returns_warn(self, deviation: Decimal) -> None:
        """Stablecoin deviation ≥ 1.5% must return WARN."""
        price = _PEG + deviation
        snapshot = make_market_snapshot(stable_price=price)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        actual_dev = abs(price - _PEG) / _PEG
        assert decision.action == RiskAction.WARN, (
            f"price={price}, deviation={actual_dev:.4%} ≥ {_WARN_THRESHOLD:.4%}:"
            f" expected WARN, got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_threshold_returns_warn(self) -> None:
        """Stablecoin deviation exactly at 1.5% must return WARN (boundary, inclusive)."""
        price = _PEG * (Decimal("1") + _WARN_THRESHOLD)
        snapshot = make_market_snapshot(stable_price=price)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.WARN, (
            f"Deviation exactly at 1.5% must return WARN, got {decision.action}"
        )

    def test_below_peg_deviation_also_triggers_warn(self) -> None:
        """Stablecoin price below peg by ≥ 1.5% must also return WARN (absolute deviation)."""
        price = _PEG - Decimal("0.02")  # 2% below peg
        snapshot = make_market_snapshot(stable_price=price)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.WARN, (
            f"Price {price} (below peg by 2%) must return WARN, got {decision.action}"
        )
