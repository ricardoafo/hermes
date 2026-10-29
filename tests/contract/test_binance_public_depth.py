"""Contract test: real Binance public depth (``data-api.binance.vision``).

A read-only GET with no credentials. It checks that the shape the depth
snapshot adapter parses still holds on the real host:

- ``/api/v3/depth`` answers an object with ``bids`` and ``asks``, each a list
  of ``[price, quantity]`` decimal strings;
- bids descend, asks ascend, and the book is not crossed.

It checks shapes only, not values. Excluded from the default run by
``-m 'not contract'``; run it with
``uv run pytest -m contract tests/contract/test_binance_public_depth.py``.

Reference: openspec/archive/353-fill-kernel-in-serve/change.md R-6;
docs/decisions/2026-08-23-contract-test-tier.md.
"""

from decimal import Decimal

import httpx
import pytest

_BASE = "https://data-api.binance.vision"
_TIMEOUT_SECONDS = 10.0
_LEVELS = 20


@pytest.mark.contract
def test_depth_answers_sorted_uncrossed_decimal_string_levels() -> None:
    """GET /api/v3/depth returns the shape the adapter parses."""
    response = httpx.get(
        f"{_BASE}/api/v3/depth",
        params={"symbol": "BTCUSDT", "limit": str(_LEVELS)},
        timeout=_TIMEOUT_SECONDS,
    )
    assert response.status_code == httpx.codes.OK, response.text[:200]
    body = response.json()
    bids = [(Decimal(p), Decimal(q)) for p, q in body["bids"]]
    asks = [(Decimal(p), Decimal(q)) for p, q in body["asks"]]
    assert (len(bids), len(asks)) == (_LEVELS, _LEVELS)
    assert [p for p, _ in bids] == sorted((p for p, _ in bids), reverse=True)
    assert [p for p, _ in asks] == sorted(p for p, _ in asks)
    assert bids[0][0] < asks[0][0]
