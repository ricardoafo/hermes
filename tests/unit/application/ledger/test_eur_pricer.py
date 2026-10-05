"""Tests for EurPricer (change trade-eur-values R-2, D-1, D-2).

Reference: ADR-016 item 5.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from hermes.application.ledger.eur_pricer import MAX_CACHED_RATE_AGE_DAYS, EurPricer
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


def test_cached_rate_never_calls_the_provider() -> None:
    """Invariant (D-1 of 668): the notify path asks the cache only, so FX can never delay a push."""
    fx = FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)})
    pricer = EurPricer(fx)
    rates = (
        pricer.cached_rate(Currency.USDT, _FRI),
        pricer.cached_rate(Currency.USDT, _MON),
        pricer.cached_rate(Currency.EUR, _FRI),
    )
    assert (rates, fx.calls) == ((None, None, Decimal(1)), [])


def test_cached_rate_serves_the_final_rate_cached_for_that_date() -> None:
    """Invariant: after a fill priced the day, a push of that day reads the same ECB rate."""
    pricer = EurPricer(FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)}))
    pricer.price(Currency.USDT, _FRI, today=_FRI)
    assert pricer.cached_rate(Currency.USDT, _FRI) == Decimal("1.1721")


def test_cached_rate_serves_a_provisional_rate_fetched_but_not_final() -> None:
    """Invariant: a rate not final for a fill is still shown by a push."""
    pricer = EurPricer(FakeFx(rates={_MON: (Decimal("1.17"), _FRI)}))
    assert pricer.price(Currency.USDT, _MON, today=_MON) is None
    assert pricer.cached_rate(Currency.USDT, _MON) == Decimal("1.17")


def test_cached_rate_prefers_the_exact_date_over_the_latest() -> None:
    """Invariant: the final rate of the asked date wins over a later provisional one."""
    pricer = EurPricer(
        FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI), _MON: (Decimal("1.30"), _MON)})
    )
    pricer.price(Currency.USDT, _FRI, today=_MON)
    pricer.price(Currency.USDT, _MON, today=_MON)
    assert pricer.cached_rate(Currency.USDT, _FRI) == Decimal("1.1721")


def test_cached_rate_goes_stale_after_the_age_limit() -> None:
    """Invariant (D-1): a rate past the age limit reads as unknown; at the limit it is served."""
    pricer = EurPricer(FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)}))
    pricer.price(Currency.USDT, _FRI, today=_FRI)
    limit = timedelta(days=MAX_CACHED_RATE_AGE_DAYS)
    assert (
        pricer.cached_rate(Currency.USDT, _FRI + limit),
        pricer.cached_rate(Currency.USDT, _FRI + limit + timedelta(days=1)),
    ) == (Decimal("1.1721"), None)


def test_cached_rate_is_per_quote_currency() -> None:
    """Invariant: a USDT rate is never served for another quote."""
    pricer = EurPricer(FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)}))
    pricer.price(Currency.USDT, _FRI, today=_FRI)
    assert [pricer.cached_rate(Currency.USDC, _FRI)] == [None]
