"""Tests for the tax Decimal context (`hermes.domain.tax.precision`)."""

from __future__ import annotations

from decimal import Decimal, getcontext

from hermes.domain.tax.precision import TAX_PRECISION, exact


def test_exact_keeps_thirty_decimal_shares_whole() -> None:
    """Inside `exact`, summing 30-decimal EUR shares loses no digits."""
    share = Decimal("1234567.123456789012345678901234567890")
    with exact():
        total = share + share + share
    assert total == Decimal("3703701.370370367037037036703703703670")


def test_default_context_would_round_the_same_sum() -> None:
    """The 28-digit default context rounds that sum, which is why `exact` exists."""
    share = Decimal("1234567.123456789012345678901234567890")
    assert share + share + share != Decimal("3703701.370370367037037036703703703670")


def test_exact_restores_the_caller_precision() -> None:
    """Leaving `exact` puts the caller's precision back."""
    before = getcontext().prec
    with exact():
        inside = getcontext().prec
    assert (inside, getcontext().prec) == (TAX_PRECISION, before)
