"""Fuzz properties for the Frankfurter FX parser (issue #842).

For any reply body, ``get_latest`` and ``get_for_date`` return an ``FxRate``
or raise ``FxRateError``, nothing else; and an accepted rate is a finite
number above zero. The seed copies the shape of
``tests/fixtures/frankfurter`` (rates as JSON numbers) with a synthetic rate.
No network: the client answers from an ``httpx.MockTransport``.
"""

import json
from datetime import date
from typing import Final

import httpx
from hypothesis import example, given
from hypothesis import strategies as st

from hermes.adapters._shared.rate_limiter import RateLimiter
from hermes.adapters.fx.frankfurter import FrankfurterAdapter, FrankfurterConfig
from hermes.domain.errors import FxRateError
from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency
from tests.unit.adapters.wire_fuzz import (
    FUZZ,
    accepted,
    foreign_error,
    mutated,
    mutated_text,
    numeric_strings,
    price_violations,
)

_REPLY: Final = {"amount": 1.0, "base": "USD", "date": "2025-01-02", "rates": {"EUR": 0.9}}
_ON: Final = date(2025, 1, 2)


def _adapter(body: bytes) -> FrankfurterAdapter:
    config = FrankfurterConfig(wire_codes=FrankfurterConfig.USDT_AS_USD)
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=body))
    client = httpx.Client(base_url=config.base_url, transport=transport)
    return FrankfurterAdapter(config=config, client=client, limiter=RateLimiter(config.rate_limit))


def _for_date(body: str | bytes) -> FxRate:
    raw = body.encode("utf-8") if isinstance(body, str) else body
    return _adapter(raw).get_for_date(Currency.USDT, Currency.EUR, _ON)


def _rate_of(value: object) -> object:
    return value.rate if isinstance(value, FxRate) else value


@FUZZ
@given(body=mutated_text(_REPLY))
@example(body='{"date": "2025-01-02", "rates": []}')
def test_a_dated_rate_reply_parses_or_raises_fx_rate_error(body: str | bytes) -> None:
    """Invariant: any reply to a dated lookup gives an FxRate or FxRateError."""
    assert foreign_error(lambda: _for_date(body), FxRateError) == ""


@FUZZ
@given(body=mutated(_REPLY))
def test_a_latest_rate_reply_parses_or_raises_fx_rate_error(body: object) -> None:
    """Invariant: any reply to a latest lookup gives an FxRate or FxRateError."""
    latest = _adapter(json.dumps(body).encode("utf-8"))
    assert foreign_error(lambda: latest.get_latest(Currency.USDT, Currency.EUR), FxRateError) == ""


@FUZZ
@given(body=mutated(_REPLY, values=st.one_of(numeric_strings, st.floats(), st.integers())))
def test_an_accepted_rate_is_finite_and_positive(body: object) -> None:
    """Invariant: a rate the parser accepts is a finite Decimal above zero."""
    rate = accepted(lambda: _for_date(json.dumps(body)), FxRateError)
    assert price_violations(_rate_of(rate)) == []
