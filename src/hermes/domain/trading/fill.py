"""Fill and Rejection result types — output of FillModelPort.fill_order.

Both :class:`Fill` and :class:`Rejection` are **expected domain outcomes**,
not errors. They form a discriminated union — ``Fill | Rejection`` — that
callers pattern-match on (``isinstance(result, Fill)`` vs
``isinstance(result, Rejection)``). Neither goes through the ``Result``
error channel because a rejection is a named business outcome (insufficient
liquidity, price drift past limit), not an infrastructure failure.

Infrastructure failures (exchange unreachable, wire-level error) are still
raised as :class:`~hermes.domain.errors.InfraError` by the adapter layer.

Domain purity: this module imports only :mod:`hermes.domain.value_objects`,
:mod:`hermes.domain.trading.order`, and stdlib. No pydantic, no httpx
(enforced by import-linter contracts ``domain-no-pydantic`` and
``domain-no-async-or-network``).

Reference: spec-2a-fill-model-port/change.md D-4 (Fill | Rejection
discriminated union — native union, not Result).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from hermes.domain.trading.order import Order
from hermes.domain.value_objects.money import Money

__all__ = ["Fill", "Rejection"]


@dataclass(frozen=True, slots=True)
class Fill:
    """Successful fill result returned by :meth:`FillModelPort.fill_order`.

    All fields are required — a partial fill is represented as a separate
    ``Fill`` covering the executed portion. The fill model is responsible
    for deciding whether a partial fill is permissible given the order's
    semantics (IOC, FOK, GTC, etc. — deferred to concrete implementations).

    Attributes:
        order: The original :class:`~hermes.domain.trading.order.Order`
            that generated this fill. Preserved for traceability — callers
            can reconstruct what was requested vs what executed.
        executed_qty: Base-asset quantity actually filled. Must be > 0 and
            <= ``order.qty.amount``. Equal to ``order.qty.amount`` for a
            full fill; less for a partial fill.
        avg_price: Volume-weighted average execution price (quote currency
            per base unit). Must be > 0.
        fee: Fee charged for this fill in the quote currency. ``Money``
            ensures currency is explicit — fees are always quote-denominated
            at the port boundary (adapters convert if the exchange charges
            in a third token like BNB).
        timestamp: UTC-aware :class:`~datetime.datetime` when the fill was
            recorded. Naive datetimes are rejected — the no-naive-datetime
            pre-commit hook enforces this at edit time.
    """

    order: Order
    executed_qty: Decimal
    avg_price: Decimal
    fee: Money
    timestamp: datetime

    def __post_init__(self) -> None:
        """Validate fill consistency invariants.

        Raises:
            ValueError: On ``executed_qty <= 0``, ``executed_qty >
                order.qty.amount``, ``avg_price <= 0``, or naive
                ``timestamp``.
        """
        if self.executed_qty <= Decimal("0"):
            raise ValueError(f"Fill.executed_qty must be > 0, got {self.executed_qty!r}")
        if self.executed_qty > self.order.qty.amount:
            raise ValueError(
                f"Fill.executed_qty {self.executed_qty!r} exceeds "
                f"order.qty {self.order.qty.amount!r}"
            )
        if self.avg_price <= Decimal("0"):
            raise ValueError(f"Fill.avg_price must be > 0, got {self.avg_price!r}")
        if self.timestamp.tzinfo is None:
            raise ValueError(f"Fill.timestamp must be UTC-aware, got naive {self.timestamp!r}")


@dataclass(frozen=True, slots=True)
class Rejection:
    """Order rejected by the fill model — an expected business outcome.

    A :class:`Rejection` is returned when the fill model determines the
    order cannot be filled under the current market conditions — for example:

    - Insufficient liquidity in the orderbook (the book cannot absorb
      ``order.qty`` without walking past an acceptable slippage threshold).
    - Limit price not reachable (the best available price is worse than
      ``order.limit_price`` for a limit order).
    - Clock is stale (the fill model detects the provided ``clock``
      timestamp is older than the orderbook snapshot, signalling a data
      delivery issue that the composition root must resolve).

    ``Rejection`` is NOT used for infrastructure failures — those raise
    :class:`~hermes.domain.errors.InfraError`.

    Attributes:
        order: The original :class:`~hermes.domain.trading.order.Order`
            that was rejected. Preserved for logging and for the caller to
            decide whether to retry, cancel, or alert.
        reason: Human-readable explanation of why the order was rejected.
            Free-text; format is implementation-specific. Callers MUST NOT
            parse or switch on this string — it is for operators only.
    """

    order: Order
    reason: str

    def __post_init__(self) -> None:
        """Validate that ``reason`` is non-empty.

        Raises:
            ValueError: On empty ``reason`` — a rejection with no explanation
                is not actionable by the operator.
        """
        if not self.reason.strip():
            raise ValueError(
                "Rejection.reason must be non-empty — provide a human-readable "
                "explanation of why the order was rejected."
            )
