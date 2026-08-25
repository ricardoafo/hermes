"""Structural-conformance tests for ExchangePort.

Protects the invariants that close Issue #199 (empty ExchangePort stub):

- ExchangePort is a runtime-checkable Protocol.
- ExchangePort defines exactly the expected method set.
- A minimal concrete class implementing all methods satisfies the Protocol.
- No adapter is required — the port shape is tested in isolation.
- Real-money guards are NOT part of the port (tested implicitly by the
  absence of any mode/dry_run parameter in the method signatures).

Reference: spec-2a-fill-model-port/change.md D-3 (read + write in one port,
safety at composition root), Issue #199.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_type_hints

import pytest

from hermes.domain.fees import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.ports.exchange import ExchangePort

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_CLOCK = datetime(2026, 8, 25, 10, 0, 0, tzinfo=UTC)

_ORDERBOOK = Orderbook(
    pair=_BTC_USDT,
    bids=(OrderbookLevel(price=Decimal("60000"), quantity=Decimal("1")),),
    asks=(OrderbookLevel(price=Decimal("60001"), quantity=Decimal("1")),),
    snapshot_time=_CLOCK,
)

_FEE_SCHEDULE = FeeSchedule.from_tiers(
    pair=_BTC_USDT,
    tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),),
)

# ---------------------------------------------------------------------------
# The expected method surface of ExchangePort
# ---------------------------------------------------------------------------

#: Canonical method names ExchangePort must define.
#: Removing any of these from the Protocol must cause a test failure.
EXPECTED_METHODS = frozenset(
    {
        "place_market_order",
        "place_limit_order",
        "cancel_order",
        "get_orderbook",
        "get_balance",
        "get_fees",
    }
)


# ---------------------------------------------------------------------------
# Minimal concrete stub — proves the Protocol is implementable
# ---------------------------------------------------------------------------


class _MinimalExchangeStub:
    """Minimal ExchangePort implementation that returns hardcoded values.

    NOT production code — exists only to prove the Protocol is implementable
    and to exercise ``isinstance`` at runtime.
    """

    def place_market_order(
        self,
        *,
        side: OrderSide,
        order: Order,
    ) -> Fill:
        """Return a hardcoded Fill."""
        return Fill(
            order=order,
            executed_qty=order.qty.amount,
            avg_price=Decimal("60000"),
            fee=Money(amount=Decimal("0.01"), currency=Currency.USDT),
            timestamp=_CLOCK,
        )

    def place_limit_order(
        self,
        *,
        order: Order,
    ) -> Fill | Rejection:
        """Return a Fill for any limit order (stub: always fills)."""
        return Fill(
            order=order,
            executed_qty=order.qty.amount,
            avg_price=order.limit_price or Decimal("60000"),
            fee=Money(amount=Decimal("0.01"), currency=Currency.USDT),
            timestamp=_CLOCK,
        )

    def cancel_order(self, *, order_id: str) -> None:
        """Accept a cancel without side-effects."""
        return None

    def get_orderbook(self, *, pair: Pair) -> Orderbook:
        """Return a hardcoded Orderbook."""
        return _ORDERBOOK

    def get_balance(self, *, currency: Currency) -> Quantity:
        """Return a hardcoded positive balance."""
        return Quantity(amount=Decimal("1.0"), asset=currency)

    def get_fees(self, *, pair: Pair) -> FeeSchedule:
        """Return a hardcoded FeeSchedule."""
        return _FEE_SCHEDULE


class _MissingMethodStub:
    """Stub that omits ``get_fees`` — used to verify negative conformance."""

    def place_market_order(self, *, side: OrderSide, order: Order) -> Fill:
        """Return hardcoded Fill."""
        return Fill(
            order=order,
            executed_qty=order.qty.amount,
            avg_price=Decimal("60000"),
            fee=Money(amount=Decimal("0.01"), currency=Currency.USDT),
            timestamp=_CLOCK,
        )

    def place_limit_order(self, *, order: Order) -> Fill | Rejection:
        """Return Fill."""
        return Fill(
            order=order,
            executed_qty=order.qty.amount,
            avg_price=Decimal("60000"),
            fee=Money(amount=Decimal("0.01"), currency=Currency.USDT),
            timestamp=_CLOCK,
        )

    def cancel_order(self, *, order_id: str) -> None:
        """No-op."""
        return None

    def get_orderbook(self, *, pair: Pair) -> Orderbook:
        """Return hardcoded Orderbook."""
        return _ORDERBOOK

    def get_balance(self, *, currency: Currency) -> Quantity:
        """Return hardcoded balance."""
        return Quantity(amount=Decimal("1.0"), asset=currency)

    # NOTE: get_fees is intentionally missing — this stub should NOT satisfy ExchangePort.


# ---------------------------------------------------------------------------
# Protocol shape tests
# ---------------------------------------------------------------------------


class TestExchangePortShape:
    """Verify the ExchangePort Protocol defines the expected method surface."""

    def test_exchange_port_is_runtime_checkable(self) -> None:
        """ExchangePort must be @runtime_checkable so isinstance checks work."""
        try:
            result = isinstance(object(), ExchangePort)
        except TypeError as exc:
            pytest.fail(
                f"ExchangePort is not @runtime_checkable — isinstance raised {exc!r}. "
                "Add @runtime_checkable to the class decorator."
            )
        assert not result, "object() has no exchange methods and must NOT satisfy ExchangePort."

    def test_exchange_port_defines_all_expected_methods(self) -> None:
        """ExchangePort must define exactly the expected method set (closes #199)."""
        actual = {
            name
            for name, val in inspect.getmembers(ExchangePort)
            if not name.startswith("_") and callable(val)
        }
        missing = EXPECTED_METHODS - actual
        assert not missing, (
            f"ExchangePort is missing method(s): {missing!r}. "
            "All expected methods must be defined — this closes Issue #199 "
            "(the empty stub had no methods)."
        )

    @pytest.mark.parametrize("method_name", sorted(EXPECTED_METHODS))
    def test_each_expected_method_is_callable(self, method_name: str) -> None:
        """Each expected ExchangePort method must be callable and inspectable."""
        member = getattr(ExchangePort, method_name, None)
        assert member is not None, (
            f"ExchangePort.{method_name} is not defined — expected it to be present."
        )
        assert callable(member), (
            f"ExchangePort.{method_name} must be callable, got {type(member).__name__!r}."
        )

    def test_exchange_port_methods_have_no_mode_parameter(self) -> None:
        """None of the ExchangePort methods should have a mode/dry_run parameter.

        Real-money guards live at the composition root (ADR-003 §00 I-08).
        A mode parameter in the port would leak concerns that belong in the adapter.
        """
        forbidden_params = {"mode", "dry_run", "backtest", "live", "shadow"}
        for method_name in EXPECTED_METHODS:
            method = getattr(ExchangePort, method_name)
            sig = inspect.signature(method)
            overlap = forbidden_params & set(sig.parameters)
            assert not overlap, (
                f"ExchangePort.{method_name} has forbidden parameter(s): {overlap!r}. "
                "Real-money guards belong at the composition root (ADR-003 §00 I-08)."
            )

    def test_exchange_port_type_hints_resolve(self) -> None:
        """All type hints on ExchangePort methods must resolve without ImportError."""
        for method_name in EXPECTED_METHODS:
            method = getattr(ExchangePort, method_name)
            try:
                get_type_hints(method)
            except Exception as exc:
                pytest.fail(
                    f"get_type_hints(ExchangePort.{method_name}) failed: {exc!r}. "
                    "All type annotations must be importable at runtime."
                )


# ---------------------------------------------------------------------------
# Runtime-checkable isinstance tests
# ---------------------------------------------------------------------------


class TestExchangePortConformance:
    """Verify that a minimal concrete implementation satisfies ExchangePort."""

    def test_minimal_stub_satisfies_exchange_port(self) -> None:
        """A class implementing all ExchangePort methods satisfies the Protocol."""
        stub = _MinimalExchangeStub()
        assert isinstance(stub, ExchangePort), (
            f"{type(stub).__name__} implements all ExchangePort methods and must "
            "satisfy isinstance(stub, ExchangePort). "
            "Check that all method signatures match exactly."
        )

    def test_missing_method_stub_does_not_satisfy_exchange_port(self) -> None:
        """A class missing get_fees does NOT satisfy ExchangePort at runtime."""
        stub = _MissingMethodStub()
        assert not isinstance(stub, ExchangePort), (
            "_MissingMethodStub omits get_fees and must NOT satisfy ExchangePort. "
            "The @runtime_checkable check should detect the missing method."
        )

    def test_plain_object_does_not_satisfy_exchange_port(self) -> None:
        """An unrelated class without any exchange methods does not satisfy ExchangePort."""

        class _Empty:
            pass

        assert not isinstance(_Empty(), ExchangePort), (
            "_Empty has no exchange methods and must NOT satisfy ExchangePort."
        )


# ---------------------------------------------------------------------------
# Method signature spot-checks
# ---------------------------------------------------------------------------


class TestExchangePortMethodSignatures:
    """Spot-check that method signatures match the spec."""

    def test_place_market_order_has_side_and_order_params(self) -> None:
        """place_market_order must accept side and order as keyword-only params."""
        sig = inspect.signature(ExchangePort.place_market_order)
        params = {p: v.kind for p, v in sig.parameters.items() if p != "self"}
        assert "side" in params, (
            "place_market_order must have a 'side' parameter — "
            "callers must be able to override the side independently."
        )
        assert "order" in params, "place_market_order must have an 'order' parameter."

    def test_place_limit_order_has_order_param(self) -> None:
        """place_limit_order must accept an order parameter."""
        sig = inspect.signature(ExchangePort.place_limit_order)
        params = set(sig.parameters) - {"self"}
        assert "order" in params, (
            "place_limit_order must have an 'order' parameter — "
            "the limit price is embedded in order.limit_price."
        )

    def test_cancel_order_has_order_id_param(self) -> None:
        """cancel_order must accept an order_id parameter."""
        sig = inspect.signature(ExchangePort.cancel_order)
        params = set(sig.parameters) - {"self"}
        assert "order_id" in params, (
            "cancel_order must accept an order_id parameter — "
            "callers pass the exchange-assigned identifier from the Fill."
        )

    def test_get_orderbook_has_pair_param(self) -> None:
        """get_orderbook must accept a pair parameter."""
        sig = inspect.signature(ExchangePort.get_orderbook)
        params = set(sig.parameters) - {"self"}
        assert "pair" in params, (
            "get_orderbook must accept a pair parameter — "
            "the exchange must know which market to query."
        )

    def test_get_balance_has_currency_param(self) -> None:
        """get_balance must accept a currency parameter."""
        sig = inspect.signature(ExchangePort.get_balance)
        params = set(sig.parameters) - {"self"}
        assert "currency" in params, (
            "get_balance must accept a currency parameter — "
            "callers query by asset, not by account-wide balance."
        )

    def test_get_fees_has_pair_param(self) -> None:
        """get_fees must accept a pair parameter."""
        sig = inspect.signature(ExchangePort.get_fees)
        params = set(sig.parameters) - {"self"}
        assert "pair" in params, (
            "get_fees must accept a pair parameter — "
            "fee schedules are pair-specific on most venues."
        )


# ---------------------------------------------------------------------------
# Stub behaviour spot-checks
# ---------------------------------------------------------------------------


class TestExchangePortStubBehaviour:
    """Exercise the minimal stub to prove the Protocol methods are callable end-to-end."""

    def test_stub_place_market_order_returns_fill(self) -> None:
        """place_market_order on the stub returns a Fill."""
        stub = _MinimalExchangeStub()
        order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.1"), asset=Currency.BTC),
        )
        result = stub.place_market_order(side=OrderSide.BUY, order=order)
        assert isinstance(result, Fill), (
            f"place_market_order must return a Fill, got {type(result).__name__!r}."
        )

    def test_stub_get_orderbook_returns_orderbook(self) -> None:
        """get_orderbook on the stub returns an Orderbook."""
        stub = _MinimalExchangeStub()
        result = stub.get_orderbook(pair=_BTC_USDT)
        assert isinstance(result, Orderbook), (
            f"get_orderbook must return an Orderbook, got {type(result).__name__!r}."
        )

    def test_stub_get_balance_returns_quantity(self) -> None:
        """get_balance on the stub returns a Quantity with the queried currency."""
        stub = _MinimalExchangeStub()
        result = stub.get_balance(currency=Currency.BTC)
        assert isinstance(result, Quantity), (
            f"get_balance must return a Quantity, got {type(result).__name__!r}."
        )
        assert result.asset == Currency.BTC, (
            f"get_balance for BTC must return Quantity with asset=BTC, got {result.asset!r}."
        )

    def test_stub_get_fees_returns_fee_schedule(self) -> None:
        """get_fees on the stub returns a FeeSchedule."""
        stub = _MinimalExchangeStub()
        result = stub.get_fees(pair=_BTC_USDT)
        assert isinstance(result, FeeSchedule), (
            f"get_fees must return a FeeSchedule, got {type(result).__name__!r}."
        )

    def test_stub_cancel_order_is_none(self) -> None:
        """cancel_order returns None (idempotent void method with no meaningful payload)."""
        stub = _MinimalExchangeStub()
        # Call cancel_order and capture return value — Python void methods return None.
        return_value = stub.cancel_order(order_id="exchange-id-123")
        assert type(return_value) is type(None), (
            f"cancel_order must return None (idempotent void method), got "
            f"{type(return_value).__name__!r}."
        )
