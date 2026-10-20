"""Contract test — signed round trip on the real Binance spot testnet (#148).

Places a tiny limit order far below the market so it rests, checks the
account answers, then cancels the order and cancels it again to prove the
idempotent path. Testnet only: the adapter refuses any other host without
``allow_mainnet``, and this test never sets it.

Needs ``HERMES_BINANCE__API_KEY`` and ``HERMES_BINANCE__API_SECRET`` for a
testnet account (testnet.binance.vision); skipped without them. Excluded from
the default run by ``-m 'not contract'``; run with
``uv run pytest -m contract tests/contract/test_binance_spot_testnet.py``.

Reference: docs/decisions/2026-08-23-contract-test-tier.md.
"""

import os
from decimal import Decimal

import pytest

from hermes.adapters.exchanges.binance_spot import BinanceSpotAdapter, BinanceSpotConfig
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

pytestmark = [
    pytest.mark.contract,
    pytest.mark.skipif(
        not os.environ.get("HERMES_BINANCE__API_KEY"),
        reason="needs HERMES_BINANCE__API_KEY and HERMES_BINANCE__API_SECRET (testnet)",
    ),
]

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)


def test_resting_stop_loss_can_be_placed_and_cancelled_twice() -> None:
    """A stop far below the market rests, cancels, and a second cancel is a no-op."""
    adapter = BinanceSpotAdapter.create(BinanceSpotConfig.from_env(os.environ))
    try:
        assert adapter.get_balance(currency=Currency.USDT).asset is Currency.USDT
        best_bid = adapter.get_orderbook(pair=_BTC).bids[0].price
        order = Order(
            side=OrderSide.SELL,
            pair=_BTC,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            strategy_id=StrategyId("contract-test"),
        )
        order_id = adapter.place_stop_loss(
            order=order, stop_price=(best_bid / 2).quantize(Decimal("0.01"))
        )
        assert order_id.startswith("BTCUSDT:")
        adapter.cancel_order(order_id=order_id)
        adapter.cancel_order(order_id=order_id)
    finally:
        adapter.close()
