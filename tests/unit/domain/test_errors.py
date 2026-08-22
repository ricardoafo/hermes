"""Tests for the HermesError hierarchy shape (R-2).

Verifies that:
- Concrete errors inherit from the expected abstract bases
- Payload fields are accessible
- String representations are human-readable

Reference: R-2 (A-2.3) — hierarchy shape via isinstance checks.
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


class TestHierarchyShape:
    """Verify isinstance relationships across the error tree."""

    def test_currency_mismatch_is_domain_error(self) -> None:
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        assert isinstance(err, DomainError)

    def test_currency_mismatch_is_hermes_error(self) -> None:
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        assert isinstance(err, HermesError)

    def test_invalid_lot_is_domain_error(self) -> None:
        err = InvalidLot(reason="qty must be > 0")
        assert isinstance(err, DomainError)

    def test_invalid_lot_is_hermes_error(self) -> None:
        err = InvalidLot(reason="qty must be > 0")
        assert isinstance(err, HermesError)

    def test_network_error_is_infra_error(self) -> None:
        err = NetworkError(msg="connection refused")
        assert isinstance(err, InfraError)

    def test_network_error_is_hermes_error(self) -> None:
        err = NetworkError(msg="connection refused")
        assert isinstance(err, HermesError)

    def test_exchange_error_is_infra_error(self) -> None:
        err = ExchangeError(msg="503 Service Unavailable")
        assert isinstance(err, InfraError)

    def test_ledger_error_is_infra_error(self) -> None:
        err = LedgerError(msg="disk full")
        assert isinstance(err, InfraError)

    def test_domain_error_is_hermes_error(self) -> None:
        # DomainError itself
        err = DomainError("oops")
        assert isinstance(err, HermesError)

    def test_infra_error_is_hermes_error(self) -> None:
        # InfraError itself
        err = InfraError("oops")
        assert isinstance(err, HermesError)


class TestCurrencyMismatchPayload:
    """Verify CurrencyMismatch payload fields are accessible."""

    def test_expected_field(self) -> None:
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        assert err.expected == "EUR"

    def test_actual_field(self) -> None:
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        assert err.actual == "BTC"

    def test_str_contains_currencies(self) -> None:
        err = CurrencyMismatch(expected="EUR", actual="BTC")
        s = str(err)
        assert "EUR" in s
        assert "BTC" in s

    def test_frozen_immutable(self) -> None:
        """CurrencyMismatch is a frozen dataclass — mutations raise."""

        err = CurrencyMismatch(expected="EUR", actual="BTC")
        with pytest.raises((AttributeError, TypeError)):
            err.expected = "USDT"  # type: ignore[misc]


class TestInvalidLotPayload:
    """Verify InvalidLot payload fields are accessible."""

    def test_reason_field(self) -> None:
        err = InvalidLot(reason="amount must be positive")
        assert err.reason == "amount must be positive"

    def test_str_contains_reason(self) -> None:
        err = InvalidLot(reason="amount must be positive")
        assert "amount must be positive" in str(err)
