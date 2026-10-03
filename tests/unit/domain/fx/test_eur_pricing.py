"""Tests for EUR pricing of trades (change trade-eur-values D-1, D-2).

Reference: ADR-002 §3, ADR-016 items 2 and 5.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.fx.eur_pricing import EurPricing, fill_date_madrid, rate_is_final
from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money

_ECB_2026_01_02 = FxRate(
    from_currency=Currency.EUR,
    to_currency=Currency.USDT,
    rate=Decimal("1.1721"),
    date=date(2026, 1, 2),
)


class TestEurPricing:
    """The rate is stored as the ECB quotes it and converts by division."""

    def test_from_fx_rate_keeps_the_ecb_figure_verbatim(self) -> None:
        """Invariant: the stored rate is the ECB number (USD per EUR), not an inverse."""
        pricing = EurPricing.from_fx_rate(_ECB_2026_01_02, source="ecb:frankfurter")
        assert (pricing.quote, pricing.rate, pricing.rate_date) == (
            Currency.USDT,
            Decimal("1.1721"),
            date(2026, 1, 2),
        )

    def test_to_eur_divides_by_the_rate(self) -> None:
        """Invariant: 1172.10 USDT at 1.1721 USD/EUR is exactly 1000 EUR."""
        pricing = EurPricing.from_fx_rate(_ECB_2026_01_02, source="ecb:frankfurter")
        assert pricing.to_eur(Money(amount=Decimal("1172.10"), currency=Currency.USDT)) == Money(
            amount=Decimal("1000"), currency=Currency.EUR
        )

    def test_to_eur_rejects_another_currency(self) -> None:
        """Invariant: a USDT pricing never converts an amount in another currency."""
        pricing = EurPricing.from_fx_rate(_ECB_2026_01_02, source="ecb:frankfurter")
        with pytest.raises(ValueError, match="pricing is for USDT"):
            pricing.to_eur(Money(amount=Decimal("1"), currency=Currency.USDC))

    def test_from_fx_rate_rejects_a_non_eur_base(self) -> None:
        """Invariant: only an EUR -> quote rate is a quote-units-per-euro figure."""
        inverse = FxRate(
            from_currency=Currency.USDT,
            to_currency=Currency.EUR,
            rate=Decimal("0.853"),
            date=date(2026, 1, 2),
        )
        with pytest.raises(ValueError, match="EUR-based rate"):
            EurPricing.from_fx_rate(inverse, source="ecb:frankfurter")

    def test_identity_prices_eur_at_one(self) -> None:
        """Invariant: an EUR-quoted trade needs no lookup and converts 1:1."""
        pricing = EurPricing.identity(date(2026, 3, 1))
        assert pricing.to_eur(Money(amount=Decimal("12.5"), currency=Currency.EUR)) == Money(
            amount=Decimal("12.5"), currency=Currency.EUR
        )

    def test_non_positive_rate_is_rejected(self) -> None:
        """Invariant: a zero rate would divide by zero; construction refuses it."""
        with pytest.raises(ValueError, match="rate must be > 0"):
            EurPricing(
                quote=Currency.USDT, rate=Decimal("0"), rate_date=date(2026, 1, 2), source="x"
            )

    def test_eur_rate_other_than_one_is_rejected(self) -> None:
        """Invariant: EUR priced in EUR is always rate 1."""
        with pytest.raises(ValueError, match="rate 1"):
            EurPricing(
                quote=Currency.EUR, rate=Decimal("2"), rate_date=date(2026, 1, 2), source="x"
            )


class TestFillDateMadrid:
    """The fill date is the Madrid calendar date (ADR-016 item 2)."""

    def test_late_utc_fill_on_new_years_eve_is_next_day_in_madrid(self) -> None:
        """Invariant: 23:30 UTC on 31 December is 1 January in Madrid."""
        assert fill_date_madrid(datetime(2025, 12, 31, 23, 30, tzinfo=UTC)) == date(2026, 1, 1)

    def test_naive_datetime_is_rejected(self) -> None:
        """Invariant: a naive time has no calendar date in Madrid."""
        with pytest.raises(ValueError, match="timezone-aware"):
            fill_date_madrid(datetime(2026, 1, 1, 12, 0))


class TestRateIsFinal:
    """A rate is stored only when it is the fill date's or the fill date is past."""

    def test_same_day_rate_is_final(self) -> None:
        """Invariant: the fill date's own rate is always final."""
        assert (
            rate_is_final(
                fill_date=date(2026, 1, 2), rate_date=date(2026, 1, 2), today=date(2026, 1, 2)
            )
            is True
        )

    def test_prior_day_rate_on_the_fill_day_is_not_final(self) -> None:
        """Invariant: before the ECB publishes, a same-day lookup is not final (no prior day)."""
        assert (
            rate_is_final(
                fill_date=date(2026, 1, 5), rate_date=date(2026, 1, 2), today=date(2026, 1, 5)
            )
            is False
        )

    @given(
        fill=st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 1, 1)),
        back=st.integers(min_value=0, max_value=10),
        ahead=st.integers(min_value=1, max_value=10),
    )
    def test_any_prior_business_day_is_final_once_the_fill_date_is_past(
        self, fill: date, back: int, ahead: int
    ) -> None:
        """Invariant: after the fill date, the closest prior business day is accepted."""
        rate_day = date.fromordinal(fill.toordinal() - back)
        today = date.fromordinal(fill.toordinal() + ahead)
        assert rate_is_final(fill_date=fill, rate_date=rate_day, today=today) is True
