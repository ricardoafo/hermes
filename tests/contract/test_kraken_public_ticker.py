"""Contract test — real Kraken ``/0/public/Ticker`` for BTC/USDT.

Reference: docs/decisions/2026-08-24-kraken-public-adapter.md,
docs/decisions/2026-08-23-contract-test-tier.md.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter
from hermes.domain.errors import KrakenPublicError
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair


@pytest.mark.contract
def test_get_ticker_against_real_kraken_returns_positive_prices() -> None:
    """Real Kraken Ticker for BTC/USDT returns positive bid, ask and last price.

    Asserts:

    - The adapter constructs against the real endpoint.
    - Best bid, best ask and last price are all positive Decimals.
    - The book is uncrossed (validated by the :class:`Ticker` invariants
      themselves at construction time).

    Kraken's BTC/USDT is one of its highest-liquidity pairs — a zero-price
    or crossed response would indicate a very serious problem upstream.
    """
    adapter = KrakenPublicAdapter.create()
    try:
        try:
            ticker = adapter.get_ticker(Pair(base=Currency.BTC, quote=Currency.USDT))
        except KrakenPublicError as exc:
            pytest.fail(f"Real Kraken Ticker failed for BTC/USDT: {exc}.")
        except httpx.HTTPError as exc:
            pytest.fail(f"Transport failure calling Kraken Ticker: {exc!r}")
        assert ticker.best_bid > 0, f"Expected positive best_bid, got {ticker.best_bid}"
        assert ticker.best_ask > 0, f"Expected positive best_ask, got {ticker.best_ask}"
        assert ticker.last_price > 0, f"Expected positive last_price, got {ticker.last_price}"
        # Book must be uncrossed — asserting the strict inequality is the
        # invariant hermes actually relies on downstream (spread-based
        # slippage models divide by (ask - bid) — a zero spread would blow
        # up). This is what makes the assertion non-vague per
        # scripts/dev/no-vague-test-assertions.py.
        assert ticker.best_ask > ticker.best_bid, (
            f"Kraken returned crossed/zero-spread book: bid={ticker.best_bid} ask={ticker.best_ask}"
        )
        # Pair round-trip check — the domain Pair we asked for is the one
        # stamped on the returned Ticker.
        assert ticker.pair == Pair(base=Currency.BTC, quote=Currency.USDT)
    finally:
        adapter.close()


@pytest.mark.contract
def test_usdt_usd_book_answers_under_the_mapped_symbol() -> None:
    """Real Kraken answers the USDT/USD book under ``USDTZUSD`` (#1158).

    The depeg rules RO-05 and RO-06 read this price before real money; a
    symbol Kraken renamed would leave both rules without a price. A pegged
    USDT sits within 5% of $1.00; a reading outside that is a depeg or a
    wrong book, and either needs a look.
    """
    adapter = KrakenPublicAdapter.create()
    try:
        try:
            price = adapter.get_usd_price(Currency.USDT)
        except KrakenPublicError as exc:
            pytest.fail(f"Real Kraken Ticker failed for USDT/USD: {exc}.")
        except httpx.HTTPError as exc:
            pytest.fail(f"Transport failure calling Kraken Ticker: {exc!r}")
        assert Decimal("0.95") < price < Decimal("1.05"), f"USDT/USD mid {price} is off the peg"
    finally:
        adapter.close()
