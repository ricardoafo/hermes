"""Structural-conformance tests for FillModelPort.

Protects the invariants that define the same-code-path guarantee:

- FillModelPort is a runtime-checkable Protocol.
- Any class with a matching ``fill_order`` signature satisfies the Protocol
  at both structural and runtime levels.
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
from returns.result import Result

from hermes.domain.fees import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.ports.fill_model import FillModelPort

# ---------------------------------------------------------------------------
# Shared fixtures — minimal valid domain objects for property tests
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)

_CLOCK = datetime(2026, 8, 25, 10, 0, 0, tzinfo=UTC)

_FEE_SCHEDULE = FeeSchedule.from_tiers(
    pair=_BTC_USDT,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
)

_ORDERBOOK = Orderbook(
    pair=_BTC_USDT,
    bids=(OrderbookLevel(price=Decimal("60000"), quantity=Decimal("1")),),
    asks=(OrderbookLevel(price=Decimal("60001"), quantity=Decimal("1")),),
    snapshot_time=_CLOCK,
)


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
# Minimal concrete stub — exercises the protocol contract from outside
# ---------------------------------------------------------------------------


class _AlwaysFullFillStub:
    """Minimal FillModelPort implementation that always returns a full fill.

    Satisfies the structural protocol check. NOT production code — exists
    only to prove the Protocol is implementable.
    """

    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection:
        """Return a fill equal to the full order quantity."""
        return Fill(
            order=order,
            executed_qty=order.qty.amount,
            avg_price=Decimal("60000"),
            fee=Money(amount=Decimal("0.01"), currency=order.pair.quote),
            timestamp=clock,
        )


class _AlwaysRejectStub:
    """Minimal FillModelPort implementation that always returns a rejection.

    Satisfies the structural protocol check. NOT production code.
    """

    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection:
        """Reject every order with a fixed reason."""
        return Rejection(order=order, reason="stub: always reject")


# ---------------------------------------------------------------------------
# Protocol shape tests
# ---------------------------------------------------------------------------


class TestFillModelPortShape:
    """Verify the FillModelPort Protocol shape matches the spec."""

    def test_fill_model_port_is_runtime_checkable(self) -> None:
        """FillModelPort must be decorated @runtime_checkable so isinstance checks work."""
        # If the Protocol is not @runtime_checkable, isinstance raises TypeError.
        try:
            result = isinstance(object(), FillModelPort)
        except TypeError as exc:
            pytest.fail(
                f"FillModelPort is not @runtime_checkable — isinstance raised {exc!r}. "
                "Add @runtime_checkable to the class decorator."
            )
        # object() has no fill_order method, so it must NOT satisfy the protocol.
        assert not result, (
            "object() should not satisfy FillModelPort — it has no fill_order method. "
            "The @runtime_checkable check may not be working correctly."
        )

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

    def test_fill_order_has_no_mode_parameter(self) -> None:
        """fill_order must NOT have a mode, dry_run, or backtest parameter.

        A mode flag inside fill_order is the antipattern that destroys the
        same-code-path guarantee. Mode selection happens at the composition
        root via dependency injection, not inside the port.
        """
        sig = inspect.signature(FillModelPort.fill_order)
        forbidden = {"mode", "dry_run", "backtest", "live", "shadow"}
        actual_params = set(sig.parameters)
        overlap = forbidden & actual_params
        assert not overlap, (
            f"fill_order has forbidden mode-selection parameters: {overlap!r}. "
            "Mode divergence must NOT appear in the port signature — this is "
            "the key architectural invariant (D-02)."
        )

    def test_fill_order_type_hints_importable(self) -> None:
        """All type hints in fill_order must resolve without ImportError."""
        try:
            hints = get_type_hints(FillModelPort.fill_order)
        except Exception as exc:
            pytest.fail(
                f"get_type_hints(FillModelPort.fill_order) failed: {exc!r}. "
                "All type annotations must be importable at runtime."
            )
        assert "return" in hints, (
            "fill_order must have a return annotation — Fill | Rejection is "
            "load-bearing for the discriminated union contract (D-4)."
        )


# ---------------------------------------------------------------------------
# Runtime-checkable isinstance tests
# ---------------------------------------------------------------------------


class TestFillModelPortConformance:
    """Verify that minimal concrete classes satisfy the Protocol at runtime."""

    def test_always_fill_stub_satisfies_protocol(self) -> None:
        """A class with a matching fill_order signature satisfies FillModelPort."""
        stub = _AlwaysFullFillStub()
        assert isinstance(stub, FillModelPort), (
            f"{type(stub).__name__} must satisfy FillModelPort — it implements "
            "fill_order with the correct signature."
        )

    def test_always_reject_stub_satisfies_protocol(self) -> None:
        """A class that always rejects also satisfies FillModelPort at the structural level."""
        stub = _AlwaysRejectStub()
        assert isinstance(stub, FillModelPort), (
            f"{type(stub).__name__} must satisfy FillModelPort — it implements "
            "fill_order with the correct signature."
        )

    def test_plain_object_does_not_satisfy_protocol(self) -> None:
        """An unrelated class without fill_order does not satisfy FillModelPort."""

        class _NoOp:
            pass

        assert not isinstance(_NoOp(), FillModelPort), (
            "_NoOp has no fill_order method and must NOT satisfy FillModelPort. "
            "The @runtime_checkable check is verifying structural compatibility."
        )

    def test_fill_model_port_stub_fill_order_returns_fill(self) -> None:
        """The full-fill stub returns a Fill — the happy-path discriminated union branch."""
        stub = _AlwaysFullFillStub()
        order = _make_order()
        result = stub.fill_order(
            order=order, orderbook=_ORDERBOOK, clock=_CLOCK, fees=_FEE_SCHEDULE
        )
        assert isinstance(result, Fill), (
            f"Expected Fill from _AlwaysFullFillStub.fill_order, "
            f"got {type(result).__name__!r}. The full-fill stub must return Fill."
        )

    def test_fill_model_port_stub_fill_order_returns_rejection(self) -> None:
        """The reject stub returns a Rejection — the rejection discriminated union branch."""
        stub = _AlwaysRejectStub()
        order = _make_order()
        result = stub.fill_order(
            order=order, orderbook=_ORDERBOOK, clock=_CLOCK, fees=_FEE_SCHEDULE
        )
        assert isinstance(result, Rejection), (
            f"Expected Rejection from _AlwaysRejectStub.fill_order, "
            f"got {type(result).__name__!r}. The reject stub must return Rejection."
        )

    def test_fill_model_port_result_is_not_result_monad(self) -> None:
        """fill_order must return Fill | Rejection directly, NOT a Result monad.

        Rejection is an expected business outcome, not an error. Using
        Result here would force callers to treat a normal business state
        as an error channel, which is architecturally wrong (D-4).
        """
        stub = _AlwaysFullFillStub()
        order = _make_order()
        result = stub.fill_order(
            order=order, orderbook=_ORDERBOOK, clock=_CLOCK, fees=_FEE_SCHEDULE
        )
        assert not isinstance(result, Result), (
            "fill_order must return Fill | Rejection directly, NOT a Result monad. "
            "Rejection is a named business outcome, not an error channel."
        )


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


# ---------------------------------------------------------------------------
# Discriminated union — isinstance narrowing
# ---------------------------------------------------------------------------


class TestDiscriminatedUnion:
    """Verify that Fill and Rejection can be narrowed via isinstance."""

    def test_fill_is_fill_not_rejection(self) -> None:
        """isinstance(Fill(...), Fill) is True and isinstance(Fill(...), Rejection) is False."""
        order = _make_order()
        result: Fill | Rejection = _make_fill(order)
        assert isinstance(result, Fill), (
            "isinstance narrowing: Fill result must satisfy isinstance(result, Fill)."
        )
        assert not isinstance(result, Rejection), (
            "isinstance narrowing: Fill result must NOT satisfy isinstance(result, Rejection)."
        )

    def test_rejection_is_rejection_not_fill(self) -> None:
        """Rejection satisfies isinstance(_, Rejection) and not isinstance(_, Fill)."""
        order = _make_order()
        result: Fill | Rejection = Rejection(order=order, reason="no liquidity")
        assert isinstance(result, Rejection), (
            "isinstance narrowing: Rejection result must satisfy isinstance(result, Rejection)."
        )
        assert not isinstance(result, Fill), (
            "isinstance narrowing: Rejection result must NOT satisfy isinstance(result, Fill)."
        )
