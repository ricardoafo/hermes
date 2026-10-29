"""Contract tests for ExchangePort.

Protects the invariants that close Issue #199 (empty ExchangePort stub):

- ExchangePort is a runtime-checkable Protocol.
- ExchangePort defines exactly the expected method set.
- Every method has the exact keyword-only parameters and annotated types
  below; a changed name, type or extra parameter breaks every adapter.
- Real-money guards are NOT part of the port: no method takes a mode or
  dry_run flag (ADR-003 §00 I-08, enforced by the exact parameter lists).

Reference: spec-2a-fill-model-port/change.md D-3 (read + write in one port,
safety at composition root), Issue #199.
"""

import inspect
from decimal import Decimal
from typing import Any, get_type_hints

import pytest

from hermes.domain.fees import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.ports.exchange import ExchangePort, StopLossPort

#: Canonical keyword-only parameters and return type of every ExchangePort method.
EXPECTED_SIGNATURES: dict[str, dict[str, Any]] = {
    "place_market_order": {"side": OrderSide, "order": Order, "return": Fill},
    "place_limit_order": {"order": Order, "return": Fill | Rejection},
    "cancel_order": {"order_id": str, "return": type(None)},
    "get_orderbook": {"pair": Pair, "return": Orderbook},
    "get_balance": {"currency": Currency, "return": Quantity},
    "get_fees": {"pair": Pair, "return": FeeSchedule},
}


def _public_methods(port: type) -> set[str]:
    return {
        name for name, val in inspect.getmembers(port) if not name.startswith("_") and callable(val)
    }


def test_exchange_port_is_runtime_checkable() -> None:
    """A bare object is not an ExchangePort, and isinstance does not raise."""
    assert isinstance(object(), ExchangePort) is False


def test_exchange_port_defines_exactly_the_expected_methods() -> None:
    """The method set is closed: a removed or added method is a port change (closes #199)."""
    assert _public_methods(ExchangePort) == set(EXPECTED_SIGNATURES)


@pytest.mark.parametrize("method_name", sorted(EXPECTED_SIGNATURES))
def test_method_parameters_are_the_expected_keyword_only_names(method_name: str) -> None:
    """Callers pass every argument by keyword, so names and kinds are part of the contract."""
    parameters = inspect.signature(getattr(ExchangePort, method_name)).parameters
    expected_names = [name for name in EXPECTED_SIGNATURES[method_name] if name != "return"]
    assert [name for name in parameters if name != "self"] == expected_names
    assert {name: p.kind for name, p in parameters.items() if name != "self"} == dict.fromkeys(
        expected_names, inspect.Parameter.KEYWORD_ONLY
    )


@pytest.mark.parametrize("method_name", sorted(EXPECTED_SIGNATURES))
def test_method_annotations_are_the_expected_types(method_name: str) -> None:
    """Annotations resolve at runtime and match the contract exactly."""
    assert get_type_hints(getattr(ExchangePort, method_name)) == EXPECTED_SIGNATURES[method_name]


def test_stop_loss_port_defines_only_place_stop_loss() -> None:
    """The exchange-held stop is a separate port so paper adapters need not implement it."""
    assert _public_methods(StopLossPort) == {"place_stop_loss"}
    assert get_type_hints(StopLossPort.place_stop_loss) == {
        "order": Order,
        "stop_price": Decimal,
        "return": str,
    }
