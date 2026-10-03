"""Contract test for the Frankfurter rate call, replayed from recorded responses.

Tier 2: ``respx`` replaces the network, everything above it is the real
adapter configured as the ledger uses it (``api.frankfurter.dev/v1``, USDT sent
as USD). Fixtures in ``tests/fixtures/frankfurter/`` were recorded on
2026-10-03 with read-only GETs::

    curl "https://api.frankfurter.dev/v1/2026-01-02?from=EUR&to=USD"   # Friday
    curl "https://api.frankfurter.dev/v1/2026-01-03?from=EUR&to=USD"   # Saturday
    curl "https://api.frankfurter.dev/v1/2026-01-02?from=EUR&to=USDT"  # 404

Reference: change ``trade-eur-values`` R-6, ADR-016 item 5.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from hermes.adapters.fx.frankfurter import FrankfurterAdapter, FrankfurterConfig
from hermes.domain.errors import FxRateError
from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).parents[3] / "fixtures" / "frankfurter"
_BASE_URL = "https://api.frankfurter.dev/v1"


def _fixture(name: str) -> Any:
    """Load one recorded response body."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _adapter(*, wire_codes: tuple[tuple[Currency, str], ...]) -> FrankfurterAdapter:
    """Build the adapter as ``hermes fx backfill`` does, on the recorded host."""
    return FrankfurterAdapter.create(FrankfurterConfig(base_url=_BASE_URL, wire_codes=wire_codes))


def test_business_day_rate_is_the_ecb_figure_for_usdt() -> None:
    """Invariant: EUR->USDT on a business day is the ECB EUR/USD figure, stamped USDT."""
    adapter = _adapter(wire_codes=FrankfurterConfig.USDT_AS_USD)
    try:
        with respx.mock(assert_all_called=True) as router:
            route = router.get(f"{_BASE_URL}/2026-01-02").mock(
                return_value=httpx.Response(200, json=_fixture("eur_usd_2026-01-02.json"))
            )
            rate = adapter.get_for_date(Currency.EUR, Currency.USDT, date(2026, 1, 2))
            params = route.calls[0].request.url.params
    finally:
        adapter.close()
    assert (params["from"], params["to"]) == ("EUR", "USD")
    assert rate == FxRate(
        from_currency=Currency.EUR,
        to_currency=Currency.USDT,
        rate=Decimal("1.1721"),
        date=date(2026, 1, 2),
    )


def test_weekend_lookup_returns_the_prior_business_day() -> None:
    """Invariant: a Saturday lookup is answered with Friday's rate and Friday's date."""
    adapter = _adapter(wire_codes=FrankfurterConfig.USDT_AS_USD)
    try:
        with respx.mock(assert_all_called=True) as router:
            router.get(f"{_BASE_URL}/2026-01-03").mock(
                return_value=httpx.Response(200, json=_fixture("eur_usd_2026-01-03_saturday.json"))
            )
            rate = adapter.get_for_date(Currency.EUR, Currency.USDT, date(2026, 1, 3))
    finally:
        adapter.close()
    assert (rate.rate, rate.date) == (Decimal("1.1721"), date(2026, 1, 2))


def test_usdt_without_the_wire_code_is_unknown_to_frankfurter() -> None:
    """Invariant: Frankfurter has no USDT; without the USD wire code the lookup fails."""
    adapter = _adapter(wire_codes=())
    try:
        with respx.mock(assert_all_called=True) as router:
            router.get(f"{_BASE_URL}/2026-01-02").mock(
                return_value=httpx.Response(
                    404, json=_fixture("eur_usdt_2026-01-02_not_found.json")
                )
            )
            with pytest.raises(FxRateError, match="HTTP 404"):
                adapter.get_for_date(Currency.EUR, Currency.USDT, date(2026, 1, 2))
    finally:
        adapter.close()
