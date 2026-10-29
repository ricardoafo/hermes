"""Tests for EurPricer (change trade-eur-values R-2, D-1, D-2).

Reference: ADR-016 item 5.
"""

from datetime import date, timedelta
from decimal import Decimal

from structlog.testing import capture_logs

from hermes.application.ledger.eur_pricer import (
    FAILURE_RETRY_SEC,
    MAX_CACHED_RATE_AGE_DAYS,
    EurPricer,
    LookupBudget,
)
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
    pricing = EurPricer(fx).price(Currency.USDT, _MON, today=_MON)
    assert (pricing, fx.calls) == (None, [(Currency.EUR, Currency.USDT, _MON)])


def test_weekend_fill_takes_friday_once_the_weekend_is_past() -> None:
    """Invariant: after the fill date, a Saturday fill is priced at Friday's rate."""
    pricing = EurPricer(FakeFx(rates={_SAT: (Decimal("1.1721"), _FRI)})).price(
        Currency.USDT, _SAT, today=_MON
    )
    assert pricing == EurPricing(
        quote=Currency.USDT, rate=Decimal("1.1721"), rate_date=_FRI, source="ecb:frankfurter"
    )


def test_a_rate_five_days_older_than_a_past_fill_stays_pending_with_a_warning() -> None:
    """Invariant (#996): a frozen source's old rate is not stored; the trade stays pending."""
    fill = date(2026, 1, 9)
    fx = FakeFx(rates={fill: (Decimal("1.17"), date(2026, 1, 4))})
    pricer = EurPricer(fx)

    with capture_logs() as logs:
        pricing = pricer.price(Currency.USDT, fill, today=date(2026, 1, 12))

    assert (pricing, [e["event"] for e in logs if e["log_level"] == "warning"]) == (
        None,
        ["eur_rate_too_old"],
    )


def test_final_rates_are_cached_per_currency_and_day() -> None:
    """Invariant (A-2.4): a second fill on the same day does not call the provider again."""
    fx = FakeFx(rates={_FRI: (Decimal("1.1721"), _FRI)})
    pricer = EurPricer(fx)
    pricer.price(Currency.USDT, _FRI, today=_FRI)
    pricer.price(Currency.USDT, _FRI, today=_FRI)
    assert fx.calls == [(Currency.EUR, Currency.USDT, _FRI)]


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
