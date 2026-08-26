"""Integration tests for SimulatedExchangeAdapter round-trips.

These tests wire the real domain objects together — SimulatedExchangeAdapter
+ OrderbookWalkFillModel + SimulatedClock — and verify the end-to-end
composition produces the correct observable state.

Testing pyramid — integration layer:

1. BUY fill → lot opened → SELL fill → lot_ledger unchanged (consume separate).
2. Multi-pair adapter: BUY BTC + BUY ETH produce independent balance entries.
3. Limit order round-trip: BUY limit at ask fills → lot opened → balance updated.
4. Clock advancing between orders: Fill.timestamp reflects each clock state.
5. FillModelPort + SimulatedExchangeAdapter are interchangeable wiring:
   same book + fees → same Fill regardless of which FillModelPort variant.

Reference: spec-2c-simulated-exchange-adapter/change.md.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hermes.adapters.clock.simulated import SimulatedClock
from hermes.adapters.exchanges.simulated import SimulatedExchangeAdapter
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)
_T0 = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)

_BTC_BOOK = Orderbook(
    pair=_BTC_USDT,
    bids=(
        OrderbookLevel(price=Decimal("60000"), quantity=Decimal("5")),
        OrderbookLevel(price=Decimal("59990"), quantity=Decimal("5")),
    ),
    asks=(
        OrderbookLevel(price=Decimal("60010"), quantity=Decimal("5")),
        OrderbookLevel(price=Decimal("60020"), quantity=Decimal("5")),
    ),
    snapshot_time=_T0,
)

_ETH_BOOK = Orderbook(
    pair=_ETH_USDT,
    bids=(OrderbookLevel(price=Decimal("3000"), quantity=Decimal("10")),),
    asks=(OrderbookLevel(price=Decimal("3001"), quantity=Decimal("10")),),
    snapshot_time=_T0,
)

_BTC_FEES = FeeSchedule.from_tiers(
    pair=_BTC_USDT,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
)

_ETH_FEES = FeeSchedule.from_tiers(
    pair=_ETH_USDT,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
)


def _make_adapter(
    clock: SimulatedClock | None = None,
    initial_balances: dict[Currency, Decimal] | None = None,
    strategy_id: StrategyId | None = None,
) -> SimulatedExchangeAdapter:
    """Build a fully-wired SimulatedExchangeAdapter for integration tests."""
    clk = clock or SimulatedClock(start=_T0)
    balances = initial_balances or {
        Currency.USDT: Decimal("500000"),
        Currency.BTC: Decimal("10"),
        Currency.ETH: Decimal("50"),
    }
    sid = strategy_id or StrategyId("integration-test")

    def _book_fn(pair: Pair) -> Orderbook:
        if pair == _ETH_USDT:
            return _ETH_BOOK
        return _BTC_BOOK

    def _fee_fn(pair: Pair) -> FeeSchedule:
        if pair == _ETH_USDT:
            return _ETH_FEES
        return _BTC_FEES

    return SimulatedExchangeAdapter(
        fill_model=OrderbookWalkFillModel(apply_slippage=False),
        orderbook_fn=_book_fn,
        clock=clk,
        fee_schedule_fn=_fee_fn,
        initial_balances=balances,
        strategy_id=sid,
        rng=random.Random(0),  # noqa: S311
    )


# ---------------------------------------------------------------------------
# Integration test 1 — BUY → lot opened → SELL → lot_ledger unchanged
# ---------------------------------------------------------------------------


def test_buy_sell_round_trip_lot_ledger() -> None:
    """BUY opens a lot; subsequent SELL does NOT add to lot_ledger.

    The lot_ledger is a BUY-only record. SELL consumption is handled by
    the tax layer (Spec 1 FIFO). The adapter is not responsible for
    marking lots as consumed — it only opens them.
    """
    adapter = _make_adapter()

    # BUY 0.5 BTC
    buy_order = Order(
        side=OrderSide.BUY,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
    )
    buy_fill = adapter.place_market_order(side=OrderSide.BUY, order=buy_order)
    assert isinstance(buy_fill, Fill), f"BUY must return Fill, got {type(buy_fill)!r}"
    assert len(adapter.lot_ledger) == 1, (
        f"lot_ledger must have 1 lot after BUY, got {len(adapter.lot_ledger)!r}"
    )

    # SELL 0.5 BTC — lot_ledger must remain at 1
    sell_order = Order(
        side=OrderSide.SELL,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
    )
    sell_fill = adapter.place_market_order(side=OrderSide.SELL, order=sell_order)
    assert isinstance(sell_fill, Fill), f"SELL must return Fill, got {type(sell_fill)!r}"
    assert len(adapter.lot_ledger) == 1, (
        f"lot_ledger must remain at 1 after SELL (SELL does not open lots), "
        f"got {len(adapter.lot_ledger)!r}"
    )


# ---------------------------------------------------------------------------
# Integration test 2 — Multi-pair: BTC BUY + ETH BUY independent balances
# ---------------------------------------------------------------------------


def test_multi_pair_independent_balances() -> None:
    """BTC and ETH fills update their respective base balances independently."""
    adapter = _make_adapter(
        initial_balances={
            Currency.USDT: Decimal("500000"),
            Currency.BTC: Decimal("0"),
            Currency.ETH: Decimal("0"),
        }
    )

    # BUY 0.2 BTC
    btc_order = Order(
        side=OrderSide.BUY,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.2"), asset=Currency.BTC),
    )
    btc_fill = adapter.place_market_order(side=OrderSide.BUY, order=btc_order)
    assert isinstance(btc_fill, Fill), f"BTC BUY must return Fill, got {type(btc_fill)!r}"

    # BUY 1 ETH
    eth_order = Order(
        side=OrderSide.BUY,
        pair=_ETH_USDT,
        qty=Quantity(amount=Decimal("1"), asset=Currency.ETH),
    )
    eth_fill = adapter.place_market_order(side=OrderSide.BUY, order=eth_order)
    assert isinstance(eth_fill, Fill), f"ETH BUY must return Fill, got {type(eth_fill)!r}"

    btc_bal = adapter.get_balance(currency=Currency.BTC).amount
    eth_bal = adapter.get_balance(currency=Currency.ETH).amount

    assert btc_bal == btc_fill.executed_qty, (
        f"BTC balance must equal BTC fill qty {btc_fill.executed_qty!r}, got {btc_bal!r}"
    )
    assert eth_bal == eth_fill.executed_qty, (
        f"ETH balance must equal ETH fill qty {eth_fill.executed_qty!r}, got {eth_bal!r}"
    )
    assert len(adapter.lot_ledger) == 2, (
        f"lot_ledger must have 2 lots (one per BUY pair), got {len(adapter.lot_ledger)!r}"
    )


# ---------------------------------------------------------------------------
# Integration test 3 — Limit BUY fills when limit >= ask
# ---------------------------------------------------------------------------


def test_limit_buy_fills_and_updates_state() -> None:
    """Limit BUY at ask price fills, opens a lot, and deducts balances."""
    adapter = _make_adapter(
        initial_balances={Currency.USDT: Decimal("100000"), Currency.BTC: Decimal("0")}
    )
    initial_usdt = adapter.get_balance(currency=Currency.USDT).amount
    limit_price = Decimal("60010")  # exactly the best ask
    order = Order(
        side=OrderSide.BUY,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.3"), asset=Currency.BTC),
        limit_price=limit_price,
    )
    result = adapter.place_limit_order(order=order)

    assert isinstance(result, Fill), (
        f"Limit BUY at ask must fill, got {type(result)!r} (reason: "
        f"{result.reason if isinstance(result, Rejection) else 'N/A'})"
    )

    # Lot must be opened
    assert len(adapter.lot_ledger) == 1, (
        f"lot_ledger must have 1 lot after limit BUY fill, got {len(adapter.lot_ledger)!r}"
    )

    # Base credited
    btc_bal = adapter.get_balance(currency=Currency.BTC).amount
    assert btc_bal == result.executed_qty, (
        f"BTC balance after limit fill must be {result.executed_qty!r}, got {btc_bal!r}"
    )

    # Quote debited
    expected_usdt = initial_usdt - result.executed_qty * result.avg_price - result.fee.amount
    actual_usdt = adapter.get_balance(currency=Currency.USDT).amount
    assert actual_usdt == expected_usdt, (
        f"USDT balance after limit fill: expected {expected_usdt!r}, got {actual_usdt!r}"
    )


# ---------------------------------------------------------------------------
# Integration test 4 — Clock advancing between orders
# ---------------------------------------------------------------------------


def test_fill_timestamp_reflects_advancing_clock() -> None:
    """Fill.timestamp tracks SimulatedClock advances between orders."""
    clock = SimulatedClock(start=_T0)
    adapter = _make_adapter(clock=clock)

    order = Order(
        side=OrderSide.BUY,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.1"), asset=Currency.BTC),
    )

    fill1 = adapter.place_market_order(side=OrderSide.BUY, order=order)
    assert fill1.timestamp == _T0, f"First fill.timestamp must be {_T0!r}, got {fill1.timestamp!r}"

    clock.advance(timedelta(minutes=5))
    t1 = clock.now()

    fill2 = adapter.place_market_order(side=OrderSide.BUY, order=order)
    assert fill2.timestamp == t1, (
        f"Second fill.timestamp must be {t1!r} (after clock advance), got {fill2.timestamp!r}"
    )

    assert fill2.timestamp > fill1.timestamp, (
        "Second fill must have a later timestamp than first fill after clock advance"
    )
