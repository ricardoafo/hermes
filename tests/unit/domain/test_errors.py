"""Tests for the HermesError hierarchy shape (R-2).

Verifies that:
- Concrete errors are caught by the handler of their own subtree only
- Payload fields are accessible
- String representations are human-readable

Reference: R-2 (A-2.3) — hierarchy shape, checked by what each handler catches.
"""

from __future__ import annotations

import pytest

from hermes.domain.errors import (
    CurrencyMismatch,
    DomainError,
    ExchangeError,
    HermesError,
    InfraError,
    InvalidLot,
    LedgerError,
    NetworkError,
)


def _handler_that_catches(err: BaseException) -> str:
    """Raise ``err`` and name the first handler, from most to least specific, that catches it."""
    try:
        raise err
    except DomainError:
        return "domain"
    except InfraError:
        return "infra"
    except HermesError:
        return "hermes"


class TestHierarchyShape:
    """Which handler catches each error: the domain subtree and the infra subtree stay apart."""

    @pytest.mark.parametrize(
        ("err", "expected"),
        [
            (CurrencyMismatch(expected="EUR", actual="BTC"), "domain"),
            (InvalidLot(reason="qty must be > 0"), "domain"),
            (DomainError("oops"), "domain"),
            (NetworkError(msg="connection refused"), "infra"),
            (ExchangeError(msg="503 Service Unavailable"), "infra"),
            (LedgerError(msg="disk full"), "infra"),
            (InfraError("oops"), "infra"),
            (HermesError("oops"), "hermes"),
        ],
        ids=lambda value: type(value).__name__ if not isinstance(value, str) else value,
    )
    def test_error_is_caught_by_its_own_subtree(self, err: HermesError, expected: str) -> None:
        """Each error meets its own subtree's handler first, never a sibling's."""
        assert _handler_that_catches(err) == expected


class TestCurrencyMismatchPayload:
    """Verify CurrencyMismatch payload fields are accessible."""

    def test_expected_field(self) -> None:
        """CurrencyMismatch.expected carries the currency that was required."""
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        assert err.expected == "EUR"

    def test_actual_field(self) -> None:
        """CurrencyMismatch.actual carries the currency that was received."""
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        assert err.actual == "BTC"

    def test_str_contains_currencies(self) -> None:
        """str(CurrencyMismatch) includes both the expected and actual currency names."""
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        assert str(err) == "Currency mismatch: expected EUR, got BTC"

    def test_frozen_immutable(self) -> None:
        """CurrencyMismatch is a frozen dataclass — mutations raise."""
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        with pytest.raises((AttributeError, TypeError)):
            err.expected = "USDT"  # type: ignore[misc]


class TestInvalidLotPayload:
    """Verify InvalidLot payload fields are accessible."""

    def test_reason_field(self) -> None:
        """InvalidLot.reason carries the human-readable explanation of the constraint violation."""
        err = InvalidLot(reason="amount must be positive")
        assert err.reason == "amount must be positive"

    def test_str_contains_reason(self) -> None:
        """str(InvalidLot) includes the reason string, making log output self-explanatory."""
        err = InvalidLot(reason="amount must be positive")
        assert str(err) == "Invalid lot: amount must be positive"
