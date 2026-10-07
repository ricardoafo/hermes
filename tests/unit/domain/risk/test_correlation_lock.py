"""Tests for RO-01 CorrelationLock rule.

Pre-registration §5.5 (signed): "if 30-day rolling correlation between two
open pairs exceeds 0.80, block new entry in the third correlated pair". The
tests pin that reading:

  - two held pairs and the candidate all correlated above 0.80 → HALT
  - any one of the three coefficients at or below 0.80 → OK
  - one held pair, however correlated → OK (the second position is allowed)
  - no candidate pair → OK (rule skipped)
  - a coefficient missing from the map counts as uncorrelated

Reference: pre-registration-v1.md §5.5; openspec/changes/822-portfolio-backtest D-2.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.correlation_lock import _CORRELATION_THRESHOLD, CorrelationLock
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.risk_overlay import RiskAction, RiskOverlayPort
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = CorrelationLock()

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)

_ABOVE = st.floats(min_value=0.8001, max_value=1.0, allow_nan=False)
_AT_OR_BELOW = st.floats(min_value=-1.0, max_value=0.80, allow_nan=False)


def _cluster(btc_eth: float, sol_btc: float, sol_eth: float) -> RiskAction:
    """Return RO-01's action for a SOL entry while BTC and ETH are held."""
    state = make_portfolio_state(
        held_pairs=frozenset({_BTC, _ETH}),
        correlations={(_BTC, _ETH): btc_eth, (_SOL, _BTC): sol_btc, (_ETH, _SOL): sol_eth},
    )
    return _RULE.evaluate(state, make_market_snapshot(candidate_pair=_SOL)).action


class TestCorrelationLockThresholdConstants:
    """Verify the pre-reg §5.5 RO-01 constant is exactly 0.80."""

    def test_correlation_threshold_is_binding(self) -> None:
        """Correlation threshold must be exactly 0.80 per pre-reg §5.5 RO-01."""
        assert _CORRELATION_THRESHOLD == 0.80, (
            f"RO-01 threshold must be exactly 0.80 (pre-reg §5.5), got {_CORRELATION_THRESHOLD}"
        )


class TestCorrelationLockNoCandidatePair:
    """Rule returns OK when no candidate pair is specified."""

    def test_no_candidate_pair_returns_ok(self) -> None:
        """Rule skips evaluation and returns OK when candidate_pair is None."""
        state = make_portfolio_state(
            held_pairs=frozenset({_BTC, _ETH}),
            correlations={(_BTC, _ETH): 0.99, (_BTC, _SOL): 0.99, (_ETH, _SOL): 0.99},
        )
        decision = _RULE.evaluate(state, make_market_snapshot(candidate_pair=None))
        assert decision.action == RiskAction.OK


class TestThirdCorrelatedPair:
    """The third entry into a cluster correlated above 0.80 is blocked."""

    # SEV-1: the signed correlation lock must block the third correlated entry (pre-reg §5.5)
    @given(a=_ABOVE, b=_ABOVE, c=_ABOVE)
    @settings(max_examples=50)
    def test_all_three_above_threshold_halts(self, a: float, b: float, c: float) -> None:
        """Held BTC and ETH above 0.80, SOL above 0.80 with both: the SOL entry is blocked."""
        assert _cluster(a, b, c) is RiskAction.HALT

    @given(low=_AT_OR_BELOW, which=st.integers(min_value=0, max_value=2))
    @settings(max_examples=50)
    def test_any_coefficient_at_or_below_threshold_allows(self, low: float, which: int) -> None:
        """If any one of the three coefficients is not above 0.80, the pairs are no cluster."""
        coefficients = [0.95, 0.95, 0.95]
        coefficients[which] = low
        assert _cluster(*coefficients) is RiskAction.OK

    def test_exactly_at_threshold_does_not_halt(self) -> None:
        """§5.5 says "exceeds": a coefficient of exactly 0.80 does not count."""
        assert _cluster(0.80, 0.95, 0.95) is RiskAction.OK

    def test_reason_names_both_open_pairs(self) -> None:
        """The HALT reason names the candidate and the two positions it would join."""
        state = make_portfolio_state(
            held_pairs=frozenset({_BTC, _ETH}),
            correlations={(_BTC, _ETH): 0.9, (_BTC, _SOL): 0.9, (_ETH, _SOL): 0.9},
        )
        decision = _RULE.evaluate(state, make_market_snapshot(candidate_pair=_SOL))
        assert decision.reason.startswith("RO-01:")
        assert all(str(p) in decision.reason for p in (_SOL, _BTC, _ETH))


class TestSecondPositionAllowed:
    """One correlated position does not block the second (the unsigned reading did)."""

    @given(coeff=st.floats(min_value=-1.0, max_value=1.0, allow_nan=False))
    @settings(max_examples=50)
    def test_one_held_pair_never_halts(self, coeff: float) -> None:
        """With only BTC held, an ETH entry is allowed at any correlation."""
        state = make_portfolio_state(
            held_pairs=frozenset({_BTC}), correlations={(_BTC, _ETH): coeff}
        )
        decision = _RULE.evaluate(state, make_market_snapshot(candidate_pair=_ETH))
        assert decision.action is RiskAction.OK

    def test_flat_portfolio_never_halts(self) -> None:
        """Nothing held: nothing to be correlated with."""
        state = make_portfolio_state(
            correlations={(_BTC, _ETH): 0.99, (_BTC, _SOL): 0.99, (_ETH, _SOL): 0.99}
        )
        decision = _RULE.evaluate(state, make_market_snapshot(candidate_pair=_SOL))
        assert decision.action is RiskAction.OK

    def test_candidate_already_held_is_not_its_own_partner(self) -> None:
        """A candidate that is also listed as held counts once, so two pairs are no cluster."""
        state = make_portfolio_state(
            held_pairs=frozenset({_BTC, _SOL}),
            correlations={(_BTC, _SOL): 0.99},
        )
        decision = _RULE.evaluate(state, make_market_snapshot(candidate_pair=_SOL))
        assert decision.action is RiskAction.OK


class TestMissingCoefficient:
    """A coefficient absent from the map counts as 0 (uncorrelated)."""

    @pytest.mark.parametrize(
        "missing", [(_BTC, _ETH), (_BTC, _SOL), (_ETH, _SOL)], ids=["held", "sol-btc", "sol-eth"]
    )
    def test_missing_coefficient_allows(self, missing: tuple[Pair, Pair]) -> None:
        """Drop any one of the three coefficients and the entry is allowed."""
        full = {(_BTC, _ETH): 0.95, (_BTC, _SOL): 0.95, (_ETH, _SOL): 0.95}
        del full[missing]
        state = make_portfolio_state(held_pairs=frozenset({_BTC, _ETH}), correlations=full)
        decision = _RULE.evaluate(state, make_market_snapshot(candidate_pair=_SOL))
        assert decision.action is RiskAction.OK

    def test_key_order_does_not_matter(self) -> None:
        """A coefficient stored as (b, a) is found for (a, b)."""
        state = make_portfolio_state(
            held_pairs=frozenset({_BTC, _ETH}),
            correlations={(_ETH, _BTC): 0.9, (_SOL, _BTC): 0.9, (_SOL, _ETH): 0.9},
        )
        decision = _RULE.evaluate(state, make_market_snapshot(candidate_pair=_SOL))
        assert decision.action is RiskAction.HALT


class TestCorrelationLockProtocolConformance:
    """Rule instance satisfies RiskOverlayPort at runtime."""

    def test_is_risk_overlay_port(self) -> None:
        """CorrelationLock must be an instance of RiskOverlayPort (runtime_checkable)."""
        assert isinstance(_RULE, RiskOverlayPort), (
            "CorrelationLock must satisfy RiskOverlayPort Protocol"
        )
