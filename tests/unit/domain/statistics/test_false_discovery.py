"""Unit tests for the Benjamini-Hochberg step (ADR-026 §5.2, issue #1059).

BH at level q over m p-values: sort them, find the largest rank k with
``p_(k) <= k * q / m``, and reject every hypothesis whose p-value is at most
``p_(k)``. Expected values below are worked from that rule by hand.
"""

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.statistics.false_discovery import benjamini_hochberg

F = Fraction
_P = st.fractions(min_value=0, max_value=1, max_denominator=1000)
_Q = F("0.05")


class TestHandComputed:
    """Rejections match the BH rule worked by hand."""

    # SEV-1: BH decides which backtest candidates may become champion (ADR-026 §5)
    def test_only_the_p_values_under_their_rank_line_are_rejected(self) -> None:
        """With q = 0.05, m = 4: lines 0.0125, 0.025, 0.0375, 0.05; only 0.01 is under its line."""
        assert benjamini_hochberg([F("0.01"), F("0.04"), F("0.03"), F("0.2")], q=_Q) == (
            True,
            False,
            False,
            False,
        )

    def test_step_up_rejects_smaller_p_values_that_missed_their_own_line(self) -> None:
        """With q = 0.05, m = 3: 0.03 > 0.0167 and 0.04 > 0.0333, but 0.045 <= 0.05, so k = 3."""
        assert benjamini_hochberg([F("0.045"), F("0.03"), F("0.04")], q=_Q) == (True, True, True)

    def test_a_p_value_exactly_on_its_line_is_rejected(self) -> None:
        """With q = 0.05, m = 2: rank 1's line is 0.025, and p = 0.025 sits on it."""
        assert benjamini_hochberg([F("0.5"), F("0.025")], q=_Q) == (False, True)

    def test_a_p_value_just_over_its_line_is_kept(self) -> None:
        """With q = 0.05, m = 2: 0.0250001 > 0.025 and 0.5 > 0.05, so nothing is rejected."""
        assert benjamini_hochberg([F("0.5"), F("0.0250001")], q=_Q) == (False, False)

    def test_tied_p_values_are_rejected_together(self) -> None:
        """With q = 0.1, m = 3: both 0.06 sit under rank 2's line 0.0667, so k = 2 takes both."""
        assert benjamini_hochberg([F("0.06"), F("0.9"), F("0.06")], q=F("0.1")) == (
            True,
            False,
            True,
        )

    def test_no_p_values_rejects_nothing(self) -> None:
        """An empty family has no discoveries."""
        assert benjamini_hochberg([], q=_Q) == ()


class TestRefusals:
    """A level or p-value outside its range is a caller error, never a silent result."""

    @pytest.mark.parametrize("q", [F(0), F(1), F("-0.1")])
    def test_q_outside_the_open_unit_interval_is_refused(self, q: Fraction) -> None:
        """BH needs 0 < q < 1; q = 0 rejects nothing and q = 1 everything."""
        with pytest.raises(ValueError, match="q must be"):
            benjamini_hochberg([F("0.01")], q=q)

    @pytest.mark.parametrize("p", [F("-0.01"), F("1.01")])
    def test_p_value_outside_the_unit_interval_is_refused(self, p: Fraction) -> None:
        """A p-value is a probability."""
        with pytest.raises(ValueError, match="p-values must be"):
            benjamini_hochberg([F("0.01"), p], q=_Q)


class TestProperties:
    """Invariants of BH that hold for every family."""

    @given(
        st.lists(_P, max_size=8).flatmap(
            lambda ps: st.tuples(st.just(ps), st.permutations(range(len(ps))))
        )
    )
    def test_order_of_the_family_does_not_change_any_decision(
        self, family: tuple[list[Fraction], list[int]]
    ) -> None:
        """A permuted family gets the same decision for each p-value."""
        p_values, order = family
        decided = dict(zip(range(len(p_values)), benjamini_hochberg(p_values, q=_Q), strict=True))
        permuted = benjamini_hochberg([p_values[i] for i in order], q=_Q)
        assert permuted == tuple(decided[i] for i in order)

    @given(st.lists(_P, min_size=1, max_size=8))
    def test_a_smaller_p_value_is_rejected_whenever_a_larger_one_is(
        self, p_values: list[Fraction]
    ) -> None:
        """The rejected set is always the smallest p-values."""
        rejected = benjamini_hochberg(p_values, q=_Q)
        kept_min = min((p for p, r in zip(p_values, rejected, strict=True) if not r), default=F(2))
        assert [p for p, r in zip(p_values, rejected, strict=True) if r and p >= kept_min] == []

    @given(st.lists(_P, min_size=1, max_size=8))
    def test_every_rejection_is_backed_by_a_rank_under_its_line(
        self, p_values: list[Fraction]
    ) -> None:
        """With k rejections, the k-th smallest p-value is at most k * q / m."""
        rejected = benjamini_hochberg(p_values, q=_Q)
        k = sum(rejected)
        if k:
            assert sorted(p_values)[k - 1] <= k * _Q / len(p_values)

    @given(st.lists(_P, min_size=1, max_size=8))
    def test_no_larger_rank_is_under_its_line(self, p_values: list[Fraction]) -> None:
        """BH steps up to the largest qualifying rank, so every rank past k is over its line."""
        rejected = benjamini_hochberg(p_values, q=_Q)
        ranked = sorted(p_values)
        m = len(p_values)
        assert [j for j in range(sum(rejected) + 1, m + 1) if ranked[j - 1] <= j * _Q / m] == []
