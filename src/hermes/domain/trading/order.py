"""Order domain type — immutable order request consumed by FillModelPort.

An :class:`Order` is the input to :meth:`~hermes.ports.fill_model.FillModelPort.fill_order`.
It represents a trader's intent: buy or sell ``qty`` of ``pair``, optionally
at a ``limit_price`` (market order when ``None``).

:class:`OrderSide` is a :class:`~enum.StrEnum` so values compare equal to
their string representations (``OrderSide.BUY == "BUY"``), which is useful
when serialising orders for logging or event persistence without extra
conversion steps.

Domain purity: this module imports only :mod:`hermes.domain.value_objects`
and stdlib. No pydantic, no httpx (enforced by import-linter contracts
``domain-no-pydantic`` and ``domain-no-async-or-network``).

Reference: spec-2a-fill-model-port/change.md D-1 (single-method protocol),
D-4 (Fill | Rejection discriminated union); ADR-011 D-03 (strategy_id on
every aggregate).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["Order", "OrderSide"]


class OrderSide(StrEnum):
    """Discriminant for the direction of an order.

    :attr:`BUY`  — acquire base currency; consumes quote currency.
    :attr:`SELL` — dispose of base currency; produces quote currency.
    """

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class Order:
    """Immutable order request — the input to :meth:`FillModelPort.fill_order`.

    An ``Order`` represents a trader's intent: ``side`` (BUY or SELL),
    ``qty`` of ``pair``, with an optional ``limit_price``. When
    ``limit_price`` is ``None`` the order is treated as a market order by
    the fill model. When ``limit_price`` is set the fill model applies
    limit-fill semantics (the order rejects if the book cannot fill at or
    better than the limit).

    Attributes:
        side: Direction of the trade — :attr:`OrderSide.BUY` or
            :attr:`OrderSide.SELL`.
        pair: Trading pair. The ``qty`` asset must match ``pair.base``.
        qty: Amount of base asset to buy or sell. Must be > 0 — a zero
            quantity order has no economic meaning.
        strategy_id: The strategy bucket that produced this order. Required
            with no default (ADR-011 D-03): an order without an owner would
            make per-strategy PnL and capital budgets unattributable.
        limit_price: Optional price cap for limit orders (quote currency
            per base unit). ``None`` → market order. Must be > 0 when
            provided — a non-positive limit price cannot appear in a real
            order book.
    """

    side: OrderSide
    pair: Pair
    qty: Quantity
    strategy_id: StrategyId
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        """Validate qty > 0 and, when present, limit_price > 0.

        Raises:
            ValueError: On ``qty.amount <= 0`` or ``limit_price <= 0``.
        """
        if self.qty.amount <= Decimal("0"):
            raise ValueError(
                f"Order.qty must be > 0, got {self.qty.amount!r} "
                f"(pair={self.pair}, side={self.side})"
            )
        if self.limit_price is not None and self.limit_price <= Decimal("0"):
            raise ValueError(
                f"Order.limit_price must be > 0 when set, got {self.limit_price!r} "
                f"(pair={self.pair}, side={self.side})"
            )
