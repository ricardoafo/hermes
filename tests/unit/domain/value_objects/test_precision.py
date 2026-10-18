"""Tests for the one exact Decimal context (`hermes.domain.value_objects.precision`).

Invariants (issue #1180): portfolio and tax arithmetic share one width, and
that width keeps the figures they produce exact. The two widths used to be 60
digits for the portfolio and 100 for tax.
"""

from decimal import Decimal, getcontext

from hermes.domain.value_objects.precision import EXACT_CONTEXT, EXACT_PRECISION, exact

_SHARE = Decimal("1234567.123456789012345678901234567890")


def test_exact_keeps_thirty_decimal_shares_whole() -> None:
    """Inside `exact`, summing 30-decimal EUR shares loses no digits."""
    with exact():
        total = _SHARE + _SHARE + _SHARE
    assert total == Decimal("3703701.370370367037037036703703703670")


def test_default_context_would_round_the_same_sum() -> None:
    """The 28-digit default context rounds that sum, which is why `exact` exists.

    Each addition rounds to 28 digits: share + share is 2469134.246913578024691357802,
    and adding share again rounds 3703701.370370367037037036703234... to 28 digits.
    """
    total = _SHARE + _SHARE + _SHARE
    assert total == Decimal("3703701.370370367037037036703")


def test_exact_restores_the_caller_precision() -> None:
    """Leaving `exact` puts the caller's precision back."""
    before = getcontext().prec
    with exact():
        inside = getcontext().prec
    assert (inside, getcontext().prec) == (100, before)


def test_the_context_and_the_block_share_one_width() -> None:
    """`EXACT_CONTEXT` (portfolio) and `exact()` (tax) compute at the same 100 digits."""
    assert (EXACT_PRECISION, EXACT_CONTEXT.prec) == (100, 100)


def test_the_context_keeps_a_product_the_old_portfolio_width_rounded() -> None:
    """A 70-digit product is exact at 100 digits; the old 60-digit context rounded it.

    35 nines squared is 10**70 - 2 * 10**35 + 1, which has 70 significant digits.
    """
    nines = Decimal("9" * 35)
    expected = Decimal(10**70 - 2 * 10**35 + 1)
    assert EXACT_CONTEXT.multiply(nines, nines) == expected
