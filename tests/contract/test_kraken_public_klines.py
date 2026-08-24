"""Contract test — real Kraken ``/0/public/OHLC`` for BTC/USDT.

Kraken's OHLC endpoint is public — no auth required. This test verifies
that a small window fetch returns at least one candle with the shape
our unit mocks assume.

Reference: docs/decisions/2026-08-24-kraken-public-adapter.md,
docs/decisions/2026-08-23-contract-test-tier.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair


@pytest.mark.contract
def test_fetch_klines_against_real_kraken_returns_at_least_one_candle() -> None:
    """Real Kraken OHLC for BTC/USDT over the last hour returns candles.

    Asserts:

    - The adapter constructs against the real endpoint.
    - A small 1-hour window (1-minute candles) returns at least one
      :class:`Kline` — Kraken has 24/7 markets and always publishes
      recent 1-minute candles for a top-liquidity pair.
    - The candle's ``open_time`` is UTC-aware — proves the timestamp
      translation survives the round trip.
    """
    adapter = KrakenPublicAdapter.create()
    try:
        now = datetime.now(UTC)
        since = now - timedelta(hours=1)
        try:
            klines = list(
                adapter.fetch_klines(
                    pair=Pair(base=Currency.BTC, quote=Currency.USDT),
                    interval=KlineInterval.M1,
                    since=since,
                    until=now,
                )
            )
        except KrakenPublicError as exc:
            pytest.fail(f"Real Kraken OHLC failed for BTC/USDT: {exc}.")
        except httpx.HTTPError as exc:
            pytest.fail(f"Transport failure calling Kraken OHLC: {exc!r}")
        assert klines, "Kraken returned no candles for a 1-hour BTC/USDT window"
        first = klines[0]
        assert first.open_time.tzinfo == UTC, "Candle open_time must be UTC-aware"
    finally:
        adapter.close()
