"""Tests for the ``assert_accepted`` helper that stands in for a bare "does not raise" test."""

import pytest

from tests.support.accepted import assert_accepted


def _accepts(value: int, *, limit: int) -> None:
    if value > limit:
        raise ValueError("over the limit")


def _returns_a_value() -> int:
    return 3


def test_a_validator_that_returns_none_is_accepted() -> None:
    """Invariant: a validator that returns ``None`` has accepted; the helper passes it on."""
    assert_accepted(_accepts, 3, limit=3)


def test_a_validator_that_raises_fails_the_test_with_its_own_error() -> None:
    """Invariant: a refusal is not swallowed, so a test of an accept case fails when it refuses."""
    with pytest.raises(ValueError, match="over the limit"):
        assert_accepted(_accepts, 4, limit=3)


def test_a_call_that_returns_a_value_is_not_an_acceptance() -> None:
    """Invariant: only ``None`` means accepted, so a function that answers is flagged."""
    with pytest.raises(AssertionError, match=r"'_returns_a_value' returned 3"):
        assert_accepted(_returns_a_value)
