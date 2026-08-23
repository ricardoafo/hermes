"""Domain tests for :class:`hermes.domain.fx.fx_rate.FxRate`.

Every test protects one invariant of the value object:

- Positive-rate invariant (rate <= 0 raises at construction).
- Distinct-currency invariant (from == to raises at construction).
- Happy-path conversion produces the expected :class:`Money`.
- Currency-mismatch in :meth:`convert` raises :class:`ValueError`.
- Frozen/hashable — matches the value-object contract.

Reference: docs/guides/ai-authored-tests.md,
docs/decisions/2026-08-23-fx-rate-source-frankfurter.md.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money

_TODAY = date(2026, 8, 23)


class TestFxRateConstruction:
    """Construction-time invariants: rate > 0 and from != to."""

    def test_zero_rate_rejected(self) -> None:
        """A zero rate raises ValueError — a 0 multiplier destroys value information."""
        with pytest.raises(ValueError, match="rate must be > 0"):
            FxRate(
                from_currency=Currency.USDT,
                to_currency=Currency.EUR,
                rate=Decimal("0"),
                date=_TODAY,
            )

    def test_negative_rate_rejected(self) -> None:
        """A negative rate raises ValueError — currency rates are always positive."""
        with pytest.raises(ValueError, match="rate must be > 0"):
            FxRate(
                from_currency=Currency.USDT,
                to_currency=Currency.EUR,
                rate=Decimal("-0.5"),
                date=_TODAY,
            )

    def test_same_from_and_to_rejected(self) -> None:
        """From == to raises ValueError — self-currency FX has no meaning."""
        with pytest.raises(ValueError, match="must differ"):
            FxRate(
                from_currency=Currency.EUR,
                to_currency=Currency.EUR,
                rate=Decimal("1.0"),
                date=_TODAY,
            )

    def test_valid_construction_succeeds(self) -> None:
        """A well-formed FxRate constructs without raising and preserves its fields."""
        fx = FxRate(
            from_currency=Currency.USDT,
            to_currency=Currency.EUR,
            rate=Decimal("0.92"),
            date=_TODAY,
        )
        assert fx.from_currency == Currency.USDT
        assert fx.to_currency == Currency.EUR
        assert fx.rate == Decimal("0.92")
        assert fx.date == _TODAY


class TestFxRateFrozen:
    """FxRate is frozen — matches the value-object contract from code-conventions §1."""

    def test_attribute_assignment_raises(self) -> None:
        """Reassigning any attribute raises FrozenInstanceError (dataclass frozen=True)."""
        fx = FxRate(
            from_currency=Currency.USDT,
            to_currency=Currency.EUR,
            rate=Decimal("0.92"),
            date=_TODAY,
        )
        with pytest.raises(FrozenInstanceError):
            fx.rate = Decimal("1.5")  # type: ignore[misc]


class TestFxRateConvert:
    """convert() multiplies amounts and stamps the target currency."""

    def test_convert_happy_path_multiplies_amount(self) -> None:
        """convert(100 USDT, USDT→EUR @ 0.92) yields 92 EUR — pure Decimal multiplication."""
        fx = FxRate(
            from_currency=Currency.USDT,
            to_currency=Currency.EUR,
            rate=Decimal("0.92"),
            date=_TODAY,
        )
        result = fx.convert(Money(amount=Decimal("100"), currency=Currency.USDT))
        assert result.currency == Currency.EUR
        assert result.amount == Decimal("92.00")

    def test_convert_preserves_full_decimal_precision(self) -> None:
        """A high-precision rate yields a high-precision output amount (no rounding)."""
        fx = FxRate(
            from_currency=Currency.USDT,
            to_currency=Currency.EUR,
            rate=Decimal("0.923456789012345"),
            date=_TODAY,
        )
        result = fx.convert(Money(amount=Decimal("100"), currency=Currency.USDT))
        assert result.amount == Decimal("92.3456789012345")

    def test_convert_wrong_currency_raises(self) -> None:
        """convert(BTC, USDT→EUR) raises ValueError — cross-rate not attempted."""
        fx = FxRate(
            from_currency=Currency.USDT,
            to_currency=Currency.EUR,
            rate=Decimal("0.92"),
            date=_TODAY,
        )
        with pytest.raises(ValueError, match="does not match from_currency"):
            fx.convert(Money(amount=Decimal("1"), currency=Currency.BTC))

    def test_convert_zero_amount_yields_zero(self) -> None:
        """Zero amount times rate is zero — but the currency stamp still flips to to_currency."""
        fx = FxRate(
            from_currency=Currency.USDT,
            to_currency=Currency.EUR,
            rate=Decimal("0.92"),
            date=_TODAY,
        )
        result = fx.convert(Money.zero(Currency.USDT))
        assert result.amount == Decimal("0.00")
        assert result.currency == Currency.EUR
