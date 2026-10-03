"""Tests for the simulated exchange's lot cost basis (change trade-eur-values D-7).

The adapter has no FX rate. Its lots carry the quote-currency basis with the
buy fee included and no EUR basis, instead of the USDT price labelled EUR.

Reference: ADR-016 item 3; issue #386 (``simulated.py`` USDT-as-EUR bug).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hermes.adapters.clock.simulated import SimulatedClock
from hermes.adapters.exchanges.simulated import SimulatedExchangeAdapter
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _buy(pair: Pair, ask: str) -> tuple[SimulatedExchangeAdapter, Money]:
    book = Orderbook(
        pair=pair,
        bids=(OrderbookLevel(price=Decimal(ask) - 1, quantity=Decimal("10")),),
        asks=(OrderbookLevel(price=Decimal(ask), quantity=Decimal("10")),),
        snapshot_time=_T0,
    )
    fees = FeeSchedule.from_tiers(
        pair=pair,
        tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
    )
    adapter = SimulatedExchangeAdapter(
        fill_model=OrderbookWalkFillModel(apply_slippage=False),
        orderbook_fn=lambda _p: book,
        clock=SimulatedClock(start=_T0),
        fee_schedule_fn=lambda _p: fees,
        initial_balances={pair.quote: Decimal("1000000")},
    )
    fill = adapter.place_market_order(
        side=OrderSide.BUY,
        order=Order(
            side=OrderSide.BUY,
            pair=pair,
            qty=Quantity(amount=Decimal("0.5"), asset=pair.base),
            strategy_id=StrategyId("swing"),
        ),
    )
    return adapter, fill.fee


def test_usdt_lot_has_no_eur_basis_and_a_fee_inclusive_usdt_basis() -> None:
    """Invariant: a BTC/USDT lot's basis is (price x qty + fee) / qty in USDT; EUR is pending."""
    adapter, fee = _buy(Pair(base=Currency.BTC, quote=Currency.USDT), "60000")
    lot = adapter.lot_ledger[0]
    expected_native = (Decimal("60000") * Decimal("0.5") + fee.amount) / Decimal("0.5")
    assert (lot.cost_basis_per_unit, lot.cost_basis_native_per_unit) == (
        None,
        Money(amount=expected_native, currency=Currency.USDT),
    )


def test_eur_quoted_lot_keeps_its_eur_basis() -> None:
    """Invariant: when the quote is EUR, the native basis is the EUR basis."""
    adapter, fee = _buy(Pair(base=Currency.BTC, quote=Currency.EUR), "50000")
    lot = adapter.lot_ledger[0]
    expected = Money(
        amount=(Decimal("50000") * Decimal("0.5") + fee.amount) / Decimal("0.5"),
        currency=Currency.EUR,
    )
    assert (lot.cost_basis_per_unit, lot.cost_basis_native_per_unit) == (expected, expected)
