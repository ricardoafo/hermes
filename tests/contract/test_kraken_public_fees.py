"""Contract test — real Kraken ``/0/private/TradeVolume`` fees for BTC/USDT (#599).

Kraken stopped publishing fee tiers in the public ``AssetPairs`` reply on
2026-10-04, so the failover adapter reads the account's own fee from the signed,
read-only ``TradeVolume`` call. This test is the drift detector for that reply.

Needs ``HERMES_KRAKEN__API_KEY`` and ``HERMES_KRAKEN__API_SECRET`` for a
read-only key (Query Funds permission only); skipped without them. Kraken has no
spot testnet, but ``TradeVolume`` reads and never trades, and the adapter is
built without ``allow_mainnet``. Excluded from the default run by
``-m 'not contract'``; run with
``uv run pytest -m contract tests/contract/test_kraken_public_fees.py``.

Reference: docs/decisions/2026-08-23-contract-test-tier.md.
"""

from __future__ import annotations

import os

import pytest

from hermes.adapters.exchanges.kraken_spot import KrakenSpotAdapter, KrakenSpotConfig
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

pytestmark = [
    pytest.mark.contract,
    pytest.mark.skipif(
        not os.environ.get("HERMES_KRAKEN__API_KEY"),
        reason="needs HERMES_KRAKEN__API_KEY and HERMES_KRAKEN__API_SECRET (read-only key)",
    ),
]


def test_trade_volume_returns_the_accounts_btc_usdt_fee() -> None:
    """The real TradeVolume reply parses into one tier with a positive taker fee.

    A taker fee of zero would mean Kraken changed the reply shape or the pair
    name, not that trading became free; maker may legitimately be zero on high
    volume tiers.
    """
    adapter = KrakenSpotAdapter.create(KrakenSpotConfig.from_env(os.environ))
    try:
        tiers = adapter.get_fees(pair=Pair(base=Currency.BTC, quote=Currency.USDT)).tiers
    finally:
        adapter.close()

    assert (len(tiers), tiers[0].taker_bps > 0, tiers[0].maker_bps >= 0) == (1, True, True)
