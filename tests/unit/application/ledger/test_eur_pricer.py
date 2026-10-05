"""Tests for EurPricer (change trade-eur-values R-2, D-1, D-2).

Reference: ADR-016 item 5.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hermes.application.ledger.eur_pricer import FAILURE_RETRY_SEC, EurPricer, LookupBudget
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
    """Invariant (A-2.2, #658): an outage never raises and a failing rate is asked once."""
    fx = FakeFx(down=True)
    pricer = EurPricer(fx)
    results = (
        pricer.price(Currency.USDT, _FRI, today=_FRI),
        pricer.price(Currency.USDT, _FRI, today=_MON),
    )
    assert (results, len(fx.calls)) == ((None, None), 1)


def test_a_failed_rate_is_retried_once_the_retry_window_has_passed() -> None:
    """Invariant (#658): the failure memo expires, so a fill after the outage is still priced."""
    now = [0.0]
    fx = FakeFx(down=True)
    pricer = EurPricer(fx, monotonic=lambda: now[0])
    pricer.price(Currency.USDT, _FRI, today=_MON)
    fx.down = False
    fx.rates[_FRI] = (Decimal("1.17"), _FRI)
    inside = pricer.price(Currency.USDT, _FRI, today=_MON)
    now[0] = FAILURE_RETRY_SEC + 1
    after = pricer.price(Currency.USDT, _FRI, today=_MON)
    assert (inside, after is not None and after.rate, len(fx.calls)) == (None, Decimal("1.17"), 2)


def test_warm_stops_at_the_call_cap_and_reports_the_skipped_keys() -> None:
    """Invariant (#658): a warm-up never makes more provider calls than its budget."""
    fx = FakeFx(down=True)
    pricer = EurPricer(fx)
    days = [date(2026, 1, d) for d in range(1, 8)]
    skipped = pricer.warm(
        [(Currency.USDT, d) for d in days], today=_MON, budget=LookupBudget(3, 60.0)
    )
    assert (skipped, len(fx.calls)) == (4, 3)


def test_warm_stops_when_the_time_budget_is_spent() -> None:
    """Invariant (#658): a spent wall-clock budget means no further provider call."""
    now = [0.0]
    fx = FakeFx(down=True)
    pricer = EurPricer(fx)
    budget = LookupBudget(100, 10.0, monotonic=lambda: now[0])
    now[0] = 10.0
    skipped = pricer.warm([(Currency.USDT, _FRI), (Currency.USDT, _SAT)], today=_MON, budget=budget)
    assert (skipped, fx.calls) == (2, [])


def test_warm_stops_when_the_stop_flag_is_set() -> None:
    """Invariant (#658): shutdown aborts a warm-up at its next lookup."""
    fx = FakeFx(down=True)
    budget = LookupBudget(100, 60.0)
    budget.stop.set()
    skipped = EurPricer(fx).warm([(Currency.USDT, _FRI)], today=_MON, budget=budget)
    assert (skipped, fx.calls) == (1, [])


def test_cached_only_view_prices_warmed_rates_and_never_calls_the_provider() -> None:
    """Invariant (#658): the view serves what warm cached and fails the rest without the network."""
    fx = FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)})
    pricer = EurPricer(fx)
    pricer.warm([(Currency.USDT, _FRI)], today=_MON, budget=LookupBudget(5, 60.0))
    view = pricer.cached_only()
    warmed = view.price(Currency.USDT, _FRI, today=_MON)
    cold = view.price(Currency.USDT, _SAT, today=_MON)
    assert (warmed is not None and warmed.rate, cold, len(fx.calls)) == (Decimal("1.1721"), None, 1)


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
