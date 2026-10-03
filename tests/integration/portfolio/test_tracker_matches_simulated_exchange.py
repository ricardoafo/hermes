"""Integration test: the portfolio tracker agrees with the simulated exchange.

Two components fold the same fills: ``SimulatedExchangeAdapter`` keeps the venue
balances, ``PortfolioTracker`` keeps the strategy's view. If they ever
disagree, a strategy would size from cash the venue does not have. This test
drives a real adapter (real ``OrderbookWalkFillModel``, real fee schedule, no
mocks) through BUY, BUY, SELL, SELL and compares the two after every fill.

Each order fits inside one book level, so the fill price is exact and the
adapter's own Decimal arithmetic does not round; the comparison is ``==``.

Reference: 308-signal-event-portfolio-state change.md R-2 (A-2.3), Risks
(two cash bookkeepers).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from returns.result import Success

from hermes.adapters.clock.simulated import SimulatedClock
from hermes.adapters.exchanges.simulated import SimulatedExchangeAdapter
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)
_STRATEGY = StrategyId("swing-donchian-4h")
_SEED_CASH = Decimal("5000")

_FEES = FeeSchedule.from_tiers(
    pair=_BTC_USDT,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
)


def _book(bid: str, ask: str) -> Orderbook:
    """Return a one-level-per-side book deep enough to absorb every order here in one level."""
    return Orderbook(
        pair=_BTC_USDT,
        bids=(OrderbookLevel(price=Decimal(bid), quantity=Decimal("5")),),
        asks=(OrderbookLevel(price=Decimal(ask), quantity=Decimal("5")),),
        snapshot_time=_T0,
    )


@pytest.mark.integration
def test_tracker_cash_and_quantity_equal_the_exchange_balances_after_every_fill() -> None:
    """Across BUY, BUY, SELL, SELL, tracker cash and quantity equal the exchange balances."""
    books = {"current": _book(bid="60000", ask="60010")}
    exchange = SimulatedExchangeAdapter(
        fill_model=OrderbookWalkFillModel(apply_slippage=False),
        orderbook_fn=lambda _pair: books["current"],
        clock=SimulatedClock(start=_T0),
        fee_schedule_fn=lambda _pair: _FEES,
        initial_balances={Currency.USDT: _SEED_CASH},
        rng=random.Random(0),  # noqa: S311
    )
    tracker = PortfolioTracker(PortfolioSnapshot.seed(_STRATEGY, Money(_SEED_CASH, Currency.USDT)))

    steps = [
        (OrderSide.BUY, "0.01", _book(bid="60000", ask="60010")),
        (OrderSide.BUY, "0.02", _book(bid="61000", ask="61020")),
        (OrderSide.SELL, "0.01", _book(bid="62000", ask="62010")),
        (OrderSide.SELL, "0.02", _book(bid="63000", ask="63010")),
    ]
    for side, qty, book in steps:
        books["current"] = book
        order = Order(
            side=side,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal(qty), asset=Currency.BTC),
            strategy_id=_STRATEGY,
        )
        fill = exchange.place_market_order(side=side, order=order)
        assert isinstance(fill, Fill)

        applied = tracker.apply_fill(fill)
        assert isinstance(applied, Success), f"tracker refused {side} {qty}: {applied}"
        snapshot = applied.unwrap()
        held = snapshot.positions.get(_BTC_USDT)

        assert snapshot.cash == exchange.get_balance(currency=Currency.USDT).amount, (
            f"cash diverged after {side} {qty}"
        )
        assert (held.quantity if held else Decimal("0")) == exchange.get_balance(
            currency=Currency.BTC
        ).amount, f"quantity diverged after {side} {qty}"

    assert tracker.open_position_count() == 0
