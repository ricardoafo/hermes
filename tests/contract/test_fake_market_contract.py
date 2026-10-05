"""Contract test: the live vendors still match the contract the fake market is held to.

The fake market (``tests/support/fake_market/``, issue #688) generates data that
passes :mod:`tests.support.fake_market.schema`. This file runs the same checks
on what Binance and Frankfurter answer today, so a format change at the vendor
fails here, in the daily contract run, instead of leaving the fake quietly out
of date.

Read-only GETs, no credentials. Shapes only: the checks report which field broke
the contract and never print a price.

Excluded from the default run by ``-m 'not contract'``; run with
``uv run pytest -m contract tests/contract/test_fake_market_contract.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from hermes.domain.market_data.kline import KlineInterval
from tests.support.fake_market import schema

_BINANCE = "https://data-api.binance.vision/api/v3"
_FRANKFURTER = "https://api.frankfurter.dev/v1"
_TIMEOUT_SECONDS = 10.0
_DEPTH_LEVELS = 20


def _get_json(url: str, params: dict[str, str] | None = None) -> object:
    """GET ``url`` and return the decoded body."""
    response = httpx.get(url, params=params, timeout=_TIMEOUT_SECONDS)
    assert response.status_code == httpx.codes.OK, f"{url} answered HTTP {response.status_code}"
    return response.json()


@pytest.mark.contract
@pytest.mark.parametrize("interval", [KlineInterval.H4, KlineInterval.D1])
def test_live_klines_meet_the_fake_market_contract(interval: KlineInterval) -> None:
    """Live klines rows pass the checks every generated row must pass."""
    rows = _get_json(
        f"{_BINANCE}/klines", {"symbol": "BTCUSDT", "interval": interval.value, "limit": "3"}
    )
    assert schema.klines_problems(rows, interval) == []


@pytest.mark.contract
def test_live_time_and_depth_meet_the_fake_market_contract() -> None:
    """Live server time and a 20-level depth snapshot pass the fake's checks."""
    time_body = _get_json(f"{_BINANCE}/time")
    depth_body = _get_json(f"{_BINANCE}/depth", {"symbol": "BTCUSDT", "limit": str(_DEPTH_LEVELS)})
    assert (schema.time_problems(time_body), schema.depth_problems(depth_body, _DEPTH_LEVELS)) == (
        [],
        [],
    )


@pytest.mark.contract
def test_live_frankfurter_rate_meets_the_fake_market_contract() -> None:
    """A dated EUR to USD rate from Frankfurter passes the fake's FX checks."""
    week_ago = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
    body = _get_json(f"{_FRANKFURTER}/{week_ago}", {"from": "EUR", "to": "USD"})
    assert schema.fx_problems(body) == []
