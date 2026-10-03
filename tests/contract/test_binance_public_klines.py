"""Contract test — real Binance public market data (``data-api.binance.vision``).

Read-only GETs, no credentials. Verifies that the shapes the live closed-bar
adapter parses still hold on the real host:

- ``/api/v3/time`` returns an integer ``serverTime`` (milliseconds);
- ``/api/v3/klines`` returns 12-field rows with integer millisecond
  timestamps, the last row being the forming candle (its ``close_time`` is
  after ``serverTime``);
- ``close_time`` is ``open_time + interval - 1 ms`` (the value the adapter
  rejects when it differs).

Shapes only, not values: a spot check of recent bars against a Vision archive
file is a manual step noted in the PR (live-bar-stream risk R-6).

Excluded from the default run by ``-m 'not contract'``; run with
``uv run pytest -m contract tests/contract/test_binance_public_klines.py``.

Reference: docs/decisions/2026-08-23-contract-test-tier.md,
docs/decisions/2026-10-02-binance-public-market-data-host.md.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval

_BASE = "https://data-api.binance.vision"
_TIMEOUT_SECONDS = 10.0
_KLINES_FIELDS = 12
_ONE_MS = timedelta(milliseconds=1)


def _get_json(path: str, params: dict[str, str] | None = None) -> object:
    """GET ``path`` on the public host and return the decoded body."""
    response = httpx.get(f"{_BASE}{path}", params=params, timeout=_TIMEOUT_SECONDS)
    assert response.status_code == httpx.codes.OK, (
        f"{path} answered HTTP {response.status_code}: {response.text[:200]}"
    )
    return response.json()


def _is_ms_integer(value: object) -> bool:
    """Return whether ``value`` is a plain integer (not a bool)."""
    return isinstance(value, int) and not isinstance(value, bool)


@pytest.mark.contract
def test_time_endpoint_returns_an_integer_server_time() -> None:
    """GET /api/v3/time answers {"serverTime": <int ms>}."""
    payload = _get_json("/api/v3/time")
    assert isinstance(payload, dict), f"expected an object, got {type(payload).__name__}"
    assert _is_ms_integer(payload.get("serverTime")), f"bad serverTime in {payload!r}"


@pytest.mark.contract
def test_klines_rows_have_twelve_fields_with_ms_integers_and_a_forming_last_row() -> None:
    """GET /api/v3/klines rows have 12 fields; the last close_time is after serverTime."""
    server_time = _get_json("/api/v3/time")
    assert isinstance(server_time, dict)
    rows = _get_json("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "4h", "limit": "3"})
    assert isinstance(rows, list)
    assert len(rows) == 3, f"asked for 3 rows, got {len(rows)}"
    for row in rows:
        assert isinstance(row, list)
        assert len(row) == _KLINES_FIELDS, f"expected {_KLINES_FIELDS} fields, got {row!r}"
        assert _is_ms_integer(row[0]), f"open_time is not an integer: {row!r}"
        assert _is_ms_integer(row[6]), f"close_time is not an integer: {row!r}"
    assert rows[-1][6] > server_time["serverTime"], (
        "the last row should be the forming candle: its close_time must be after serverTime"
    )


@pytest.mark.contract
@pytest.mark.parametrize("interval", [KlineInterval.H4, KlineInterval.D1])
def test_klines_close_time_is_open_plus_interval_minus_one_millisecond(
    interval: KlineInterval,
) -> None:
    """Every row's close_time equals open_time + interval - 1 ms (the inclusive convention)."""
    rows = _get_json(
        "/api/v3/klines", {"symbol": "BTCUSDT", "interval": interval.value, "limit": "3"}
    )
    assert isinstance(rows, list)
    duration_ms = interval_duration(interval) // _ONE_MS
    mismatches = [row for row in rows if row[6] != row[0] + duration_ms - 1]
    assert mismatches == [], f"close_time convention changed for {interval}: {mismatches!r}"
