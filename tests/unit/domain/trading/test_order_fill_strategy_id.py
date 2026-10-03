"""Tests that Order and Fill carry the strategy that produced them.

Reference: ADR-011 D-03, 323-order-fill-strategy-id change.md D-1 and D-2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2026, 10, 1, tzinfo=UTC)
_SWING = StrategyId("swing-donchian-4h")

strategy_ids = st.from_regex(r"[A-Za-z0-9_.-]{1,64}", fullmatch=True).map(StrategyId)


def _order(strategy_id: StrategyId = _SWING) -> Order:
    return Order(
        side=OrderSide.BUY,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
        strategy_id=strategy_id,
    )


def _fill(order: Order) -> Fill:
    return Fill(
        order=order,
        executed_qty=order.qty.amount,
        avg_price=Decimal("60000"),
        fee=Money(amount=Decimal("1"), currency=Currency.USDT),
        timestamp=_T0,
    )


def test_order_without_strategy_id_is_rejected_at_construction() -> None:
    """An Order cannot be built without a strategy_id (ADR-011 D-03: no default, no orphans)."""
    kwargs: dict[str, Any] = {
        "side": OrderSide.BUY,
        "pair": _BTC_USDT,
        "qty": Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
    }
    with pytest.raises(TypeError, match="strategy_id"):
        Order(**kwargs)


def test_order_keeps_the_strategy_id_it_was_built_with() -> None:
    """Order.strategy_id is exactly the value passed at submission."""
    assert _order(_SWING).strategy_id == StrategyId("swing-donchian-4h")


def test_orders_differing_only_in_strategy_id_are_not_equal() -> None:
    """strategy_id takes part in Order equality, so two strategies never share an order."""
    assert _order(StrategyId("a")) != _order(StrategyId("b"))


@pytest.mark.property
@given(strategy_id=strategy_ids)
def test_fill_strategy_id_always_equals_its_orders(strategy_id: StrategyId) -> None:
    """For any strategy id, Fill.strategy_id is the strategy_id of Fill.order."""
    order = _order(strategy_id)
    assert _fill(order).strategy_id == order.strategy_id


def test_fill_rejects_a_strategy_id_that_could_disagree_with_its_order() -> None:
    """Fill takes no strategy_id argument: it can only inherit its order's."""
    kwargs: dict[str, Any] = {
        "order": _order(StrategyId("a")),
        "executed_qty": Decimal("0.5"),
        "avg_price": Decimal("60000"),
        "fee": Money(amount=Decimal("1"), currency=Currency.USDT),
        "timestamp": _T0,
        "strategy_id": StrategyId("b"),
    }
    with pytest.raises(TypeError, match="strategy_id"):
        Fill(**kwargs)


def test_rejection_exposes_the_strategy_of_the_rejected_order_via_its_order() -> None:
    """A Rejection keeps the order, so the rejected strategy stays attributable."""
    rejection = Rejection(order=_order(_SWING), reason="no liquidity")
    assert rejection.order.strategy_id == StrategyId("swing-donchian-4h")
