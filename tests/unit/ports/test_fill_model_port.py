"""Structural-conformance tests for FillModelPort.

Protects the invariants that define the same-code-path guarantee:

- FillModelPort is a runtime-checkable Protocol.
- ``fill_order`` takes exactly ``order, orderbook, clock, fees``.
- ``Fill`` and ``Rejection`` are the only valid return types — not ``Result``.
- ``Order`` shape invariants: side, pair, qty, optional limit_price.
- The Protocol does NOT leak any mode-selection (backtest/shadow/live) into
  its signature — mode divergence is entirely the composition root's concern.

These tests are intentionally implementation-agnostic: they exercise the
contract itself, never any concrete fill model. The concrete
``OrderbookWalkFillModel`` (Spec 2b) will have its own test module.

Reference: spec-2a-fill-model-port/change.md D-1 (single-method protocol),
D-2 (composition-root injection, no mode flag), D-4 (Fill | Rejection union).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_type_hints

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
from hermes.ports.fill_model import FillModelPort

_STRATEGY_ID = StrategyId("test-strategy")

# ---------------------------------------------------------------------------
# Shared fixtures — minimal valid domain objects for property tests
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)

_CLOCK = datetime(2026, 8, 25, 10, 0, 0, tzinfo=UTC)


def _make_order(
    *,
    side: OrderSide = OrderSide.BUY,
    pair: Pair | None = None,
    qty_amount: Decimal = Decimal("0.1"),
    limit_price: Decimal | None = None,
) -> Order:
    """Build a minimal valid Order for tests."""
    p = pair or _BTC_USDT
    return Order(
        side=side,
        pair=p,
        qty=Quantity(amount=qty_amount, asset=p.base),
        limit_price=limit_price,
        strategy_id=_STRATEGY_ID,
    )


def _make_fill(order: Order) -> Fill:
    """Build a minimal valid Fill for a given order."""
    return Fill(
        order=order,
        executed_qty=order.qty.amount,
        avg_price=Decimal("60000"),
        fee=Money(amount=Decimal("0.1560"), currency=Currency.USDT),
        timestamp=_CLOCK,
    )


# ---------------------------------------------------------------------------
# Protocol shape tests
# ---------------------------------------------------------------------------


class TestFillModelPortShape:
    """Verify the FillModelPort Protocol shape matches the spec."""

    def test_fill_model_port_is_runtime_checkable(self) -> None:
        """A bare object is not a FillModelPort, and isinstance does not raise."""
        assert isinstance(object(), FillModelPort) is False

    def test_fill_model_port_has_fill_order_method(self) -> None:
        """FillModelPort must expose exactly one method: fill_order."""
        members = {
            name
            for name, val in inspect.getmembers(FillModelPort)
            if not name.startswith("_") and callable(val)
        }
        assert "fill_order" in members, (
            "FillModelPort must define fill_order — the same-code-path invariant "
            "requires a single method shared by backtest, shadow, and live modes."
        )

    def test_fill_order_parameter_count(self) -> None:
        """fill_order must accept exactly 4 parameters beyond self."""
        _expected_param_count = 4
        sig = inspect.signature(FillModelPort.fill_order)
        params = [p for p in sig.parameters if p != "self"]
        assert len(params) == _expected_param_count, (
            f"fill_order must have exactly {_expected_param_count} parameters "
            f"(order, orderbook, clock, fees), "
            f"got {len(params)}: {params}. A mode parameter would violate the "
            "same-code-path invariant (D-02)."
        )

    def test_fill_order_parameter_names(self) -> None:
        """fill_order parameter names must match the spec exactly."""
        sig = inspect.signature(FillModelPort.fill_order)
        params = [p for p in sig.parameters if p != "self"]
        assert params == ["order", "orderbook", "clock", "fees"], (
            f"fill_order parameter names {params!r} do not match expected "
            "['order', 'orderbook', 'clock', 'fees']. "
            "Parameter names are part of the port contract — callers use keyword args."
        )

    def test_fill_order_returns_fill_or_rejection_not_result(self) -> None:
        """fill_order is annotated ``Fill | Rejection`` directly, not wrapped in a Result.

        Rejection is an expected business outcome, not an error channel (D-4).
        """
        assert get_type_hints(FillModelPort.fill_order)["return"] == Fill | Rejection


# ---------------------------------------------------------------------------
# Order shape invariant tests (property-based)
# ---------------------------------------------------------------------------


class TestOrderShapeInvariants:
    """Property tests for Order field constraints."""

    @given(
        qty_amount=st.decimals(
            min_value="0.0001", max_value="999999", allow_nan=False, allow_infinity=False
        )
    )
    def test_order_qty_must_be_positive(self, qty_amount: Decimal) -> None:
        """Order.qty.amount must be > 0 for any valid order.

        Zero or negative quantities have no economic meaning — a zero-qty
        buy is not a buy.
        """
        order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=qty_amount, asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
        )
        assert order.qty.amount > Decimal("0"), (
            f"Order with qty_amount={qty_amount!r} has non-positive qty. "
            "Order.qty.amount must be > 0."
        )

    @given(
        limit=st.decimals(
            min_value="0.01", max_value="9999999", allow_nan=False, allow_infinity=False
        )
    )
    def test_order_limit_price_when_set_must_be_positive(self, limit: Decimal) -> None:
        """Order.limit_price, when set, must be > 0.

        A zero or negative limit price cannot appear in a real order book.
        """
        order = Order(
            side=OrderSide.SELL,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.1"), asset=Currency.BTC),
            limit_price=limit,
            strategy_id=_STRATEGY_ID,
        )
        assert order.limit_price is not None, (
            "limit_price was provided but is None after construction."
        )
        assert order.limit_price > Decimal("0"), (
            f"Order.limit_price {order.limit_price!r} must be > 0."
        )

    def test_order_without_limit_price_is_market_order(self) -> None:
        """Order with limit_price=None reports is_market=True."""
        order = _make_order(limit_price=None)
        assert order.is_market, (
            "Order without limit_price must report is_market=True — "
            "this property drives fill-model dispatch."
        )

    def test_order_with_limit_price_is_not_market_order(self) -> None:
        """Order with limit_price set reports is_market=False."""
        order = _make_order(limit_price=Decimal("59000"))
        assert not order.is_market, (
            "Order with limit_price must report is_market=False — "
            "this property drives fill-model dispatch."
        )

    @given(side=st.sampled_from(list(OrderSide)))
    def test_order_side_is_buy_or_sell(self, side: OrderSide) -> None:
        """OrderSide must be BUY or SELL — no other values exist."""
        order = Order(
            side=side,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.01"), asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
        )
        assert order.side in (OrderSide.BUY, OrderSide.SELL), (
            f"Order.side {order.side!r} is not BUY or SELL — "
            "OrderSide is a closed enum with exactly two values."
        )

    def test_order_is_frozen(self) -> None:
        """Order must be immutable — frozen dataclass invariant."""
        order = _make_order()
        with pytest.raises((AttributeError, TypeError)):
            order.side = OrderSide.SELL  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Fill shape invariant tests
# ---------------------------------------------------------------------------


class TestFillShapeInvariants:
    """Verify Fill construction constraints."""

    def test_fill_executed_qty_must_be_positive(self) -> None:
        """Fill.executed_qty must be > 0 — a zero executed qty is not a fill."""
        order = _make_order()
        with pytest.raises(ValueError, match="executed_qty must be > 0"):
            Fill(
                order=order,
                executed_qty=Decimal("0"),
                avg_price=Decimal("60000"),
                fee=Money(amount=Decimal("0"), currency=Currency.USDT),
                timestamp=_CLOCK,
            )

    def test_fill_executed_qty_cannot_exceed_order_qty(self) -> None:
        """Fill.executed_qty must not exceed Order.qty.amount — overfill is a bug."""
        order = _make_order(qty_amount=Decimal("0.1"))
        with pytest.raises(ValueError, match=r"exceeds order\.qty"):
            Fill(
                order=order,
                executed_qty=Decimal("0.2"),
                avg_price=Decimal("60000"),
                fee=Money(amount=Decimal("0"), currency=Currency.USDT),
                timestamp=_CLOCK,
            )

    def test_fill_avg_price_must_be_positive(self) -> None:
        """Fill.avg_price must be > 0 — a zero or negative price is non-physical."""
        order = _make_order()
        with pytest.raises(ValueError, match="avg_price must be > 0"):
            Fill(
                order=order,
                executed_qty=order.qty.amount,
                avg_price=Decimal("0"),
                fee=Money(amount=Decimal("0"), currency=Currency.USDT),
                timestamp=_CLOCK,
            )

    def test_fill_timestamp_must_be_utc_aware(self) -> None:
        """Fill.timestamp must be UTC-aware — naive datetimes are not valid here."""
        order = _make_order()
        naive_clock = datetime(2026, 8, 25, 10, 0, 0)  # no tzinfo
        with pytest.raises(ValueError, match="UTC-aware"):
            Fill(
                order=order,
                executed_qty=order.qty.amount,
                avg_price=Decimal("60000"),
                fee=Money(amount=Decimal("0"), currency=Currency.USDT),
                timestamp=naive_clock,
            )

    def test_fill_is_frozen(self) -> None:
        """Fill must be immutable — frozen dataclass invariant."""
        order = _make_order()
        fill = _make_fill(order)
        with pytest.raises((AttributeError, TypeError)):
            fill.avg_price = Decimal("1")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Rejection shape invariant tests
# ---------------------------------------------------------------------------


class TestRejectionShapeInvariants:
    """Verify Rejection construction constraints."""

    def test_rejection_reason_must_be_non_empty(self) -> None:
        """Rejection.reason must not be empty — an unexplained rejection is not actionable."""
        order = _make_order()
        with pytest.raises(ValueError, match="must be non-empty"):
            Rejection(order=order, reason="")

    def test_rejection_reason_whitespace_only_is_rejected(self) -> None:
        """Rejection.reason that is whitespace-only is treated as empty."""
        order = _make_order()
        with pytest.raises(ValueError, match="must be non-empty"):
            Rejection(order=order, reason="   ")

    @given(reason=st.text(min_size=1).filter(lambda s: s.strip()))
    def test_rejection_accepts_any_non_empty_reason(self, reason: str) -> None:
        """Any non-empty reason string produces a valid Rejection."""
        order = _make_order()
        rejection = Rejection(order=order, reason=reason)
        assert rejection.reason == reason, (
            f"Rejection.reason {rejection.reason!r} does not match provided {reason!r}."
        )

    def test_rejection_preserves_original_order(self) -> None:
        """Rejection.order must be the exact Order that was rejected (identity preserved)."""
        order = _make_order()
        rejection = Rejection(order=order, reason="insufficient liquidity")
        assert rejection.order is order, (
            "Rejection.order must reference the original Order object — "
            "callers use it to decide whether to retry or alert."
        )

    def test_rejection_is_frozen(self) -> None:
        """Rejection must be immutable — frozen dataclass invariant."""
        order = _make_order()
        rejection = Rejection(order=order, reason="test reason")
        with pytest.raises((AttributeError, TypeError)):
            rejection.reason = "mutated"  # type: ignore[misc]
