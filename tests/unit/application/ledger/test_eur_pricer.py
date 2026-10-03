"""Tests for EurPricer (change trade-eur-values R-2, D-1, D-2).

Reference: ADR-016 item 5.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.fx.eur_pricing import EurPricing
from hermes.domain.value_objects.currency import Currency
from tests.unit.application.ledger.fake_fx import FakeFx

_FRI = date(2026, 1, 2)
_SAT = date(2026, 1, 3)
_MON = date(2026, 1, 5)


def test_final_rate_is_returned_as_ecb_pricing() -> None:
    """Invariant: the fill date's rate becomes the pricing, quoted USDT per EUR."""
    pricer = EurPricer(FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)}))
    assert pricer.price(Currency.USDT, _FRI, today=_FRI) == EurPricing(
        quote=Currency.USDT, rate=Decimal("1.1721"), rate_date=_FRI, source="ecb:frankfurter"
    )


def test_lookup_asks_for_eur_to_quote() -> None:
    """Invariant (D-1): the provider is asked EUR -> quote, the ECB quotation direction."""
    fx = FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)})
    EurPricer(fx).price(Currency.USDT, _FRI, today=_FRI)
    assert fx.calls == [(Currency.EUR, Currency.USDT, _FRI)]


def test_provider_failure_yields_none_without_raising() -> None:
    """Invariant (A-2.2): an FX outage never raises, and a failure is retried, not cached."""
    fx = FakeFx(down=True)
    pricer = EurPricer(fx)
    results = (
        pricer.price(Currency.USDT, _FRI, today=_FRI),
        pricer.price(Currency.USDT, _FRI, today=_MON),
    )
    assert (results, len(fx.calls)) == ((None, None), 2)


def test_prior_day_rate_on_the_fill_day_is_not_final() -> None:
    """Invariant (A-2.3): a same-day lookup that resolves to an earlier date is not stored."""
    fx = FakeFx(rates={_MON: (Decimal("1.17"), _FRI)})
    assert EurPricer(fx).price(Currency.USDT, _MON, today=_MON) is None
    assert len(fx.calls) == 1


def test_weekend_fill_takes_friday_once_the_weekend_is_past() -> None:
    """Invariant: after the fill date, a Saturday fill is priced at Friday's rate."""
    pricing = EurPricer(FakeFx(rates={_SAT: (Decimal("1.1721"), _FRI)})).price(
        Currency.USDT, _SAT, today=_MON
    )
    assert pricing == EurPricing(
        quote=Currency.USDT, rate=Decimal("1.1721"), rate_date=_FRI, source="ecb:frankfurter"
    )


def test_final_rates_are_cached_per_currency_and_day() -> None:
    """Invariant (A-2.4): a second fill on the same day does not call the provider again."""
    fx = FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)})
    pricer = EurPricer(fx)
    pricer.price(Currency.USDT, _FRI, today=_FRI)
    pricer.price(Currency.USDT, _FRI, today=_FRI)
    assert len(fx.calls) == 1


def test_eur_quote_needs_no_lookup() -> None:
    """Invariant: an EUR-quoted fill is priced at 1 without touching the provider."""
    fx = FakeFx(down=True)
    pricing = EurPricer(fx).price(Currency.EUR, _FRI, today=_FRI)
    assert (pricing, fx.calls) == (EurPricing.identity(_FRI), [])
