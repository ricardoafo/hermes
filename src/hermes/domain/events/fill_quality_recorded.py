"""FillQualityRecorded — how one simulated shadow fill compares with the §6.2 model.

``hermes serve`` writes one of these next to every ``TradeExecuted`` it
records, under the same ``correlation_id``. The rows are the per-fill input of
the SHADOW_CERTIFIED gate (pre-registration §7: sim-vs-live drift below 30 bps
at p50 over 90 days) and of risk rule RO-03 (last single-fill slippage).

The event holds execution quality only: prices of one order, never equity,
P&L or position value.

Reference: openspec/archive/353-fill-kernel-in-serve/change.md D-2, D-4;
code-conventions.md §10 (event schema versioning).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["BookSource", "FillQualityRecorded", "upcast_fill_quality_payload"]


class BookSource(StrEnum):
    """Which order book the fill walked."""

    LIVE = "live"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True, slots=True)
class FillQualityRecorded(DomainEvent):
    """Execution quality of one simulated fill.

    Fields:
        strategy_id: Strategy bucket of the fill.
        pair: Pair of the fill.
        side: BUY or SELL.
        qty: Executed base quantity.
        book_source: ``live`` (a venue snapshot) or ``synthetic`` (the §6.2
            book, used when no fresh live book exists). Only ``live`` rows
            enter the drift gate.
        book_time: Time stamped on the book the fill walked.
        fill_price: Average fill price.
        model_price: The §6.2 model's price for the same order, or ``None``
            when the model book could not fill it.
        drift_bps: Signed drift of ``fill_price`` from ``model_price`` in bps,
            positive when the fill cost more; ``None`` with ``model_price``.
        book_mid: Mid of the book walked, or ``None`` for a one-sided book.
        slippage: Signed slippage from ``book_mid`` as a fraction; ``None``
            with ``book_mid``.
        arrival_price: Close of the signal bar, the price when the strategy
            decided; ``None`` on schema v1 rows. Recorded only: no fill,
            drift or metric reads it.
        delta_ms: Whole milliseconds from the signal bar's close to the fill's
            ``occurred_at``; ``None`` on schema v1 rows. Recorded only.

    Raises:
        ValueError: On a naive time, a non-positive price or quantity, a
            drift (slippage) without its model price (mid), a non-positive
            ``arrival_price`` or a negative ``delta_ms``.
    """

    __event_schema_version__: ClassVar[int] = 2

    strategy_id: StrategyId
    pair: Pair
    side: TradeSide
    qty: Decimal
    book_source: BookSource
    book_time: datetime
    fill_price: Decimal
    model_price: Decimal | None
    drift_bps: Decimal | None
    book_mid: Decimal | None
    slippage: Decimal | None
    arrival_price: Decimal | None = None
    delta_ms: int | None = None

    def __post_init__(self) -> None:
        """Compute the payload hash, then validate the fields.

        Raises:
            ValueError: See the class docstring.
        """
        DomainEvent.__post_init__(self)
        for name, moment in (("occurred_at", self.occurred_at), ("book_time", self.book_time)):
            if moment.tzinfo is None:
                raise ValueError(f"FillQualityRecorded.{name} must be UTC-aware")
        if self.qty <= 0 or self.fill_price <= 0:
            raise ValueError(
                f"FillQualityRecorded needs qty and fill_price > 0, got {self.qty}, "
                f"{self.fill_price}"
            )
        if (self.model_price is None) != (self.drift_bps is None):
            raise ValueError("FillQualityRecorded.drift_bps needs model_price, and vice versa")
        if (self.book_mid is None) != (self.slippage is None):
            raise ValueError("FillQualityRecorded.slippage needs book_mid, and vice versa")
        if self.arrival_price is not None and self.arrival_price <= 0:
            raise ValueError(
                f"FillQualityRecorded.arrival_price must be > 0, got {self.arrival_price}"
            )
        if self.delta_ms is not None and self.delta_ms < 0:
            raise ValueError(f"FillQualityRecorded.delta_ms must be >= 0, got {self.delta_ms}")


def upcast_fill_quality_payload(version: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Lift a stored ``FillQualityRecorded`` payload to the current schema version.

    Args:
        version: ``event_schema_version`` the row was written with.
        payload: Decoded ``payload_json`` of the row.

    Returns:
        A payload carrying every field of schema v2; v1 rows get
        ``arrival_price`` and ``delta_ms`` as ``None``.

    Raises:
        ValueError: ``version`` is not a known schema version.
    """
    if version == 1:
        return {**payload, "arrival_price": None, "delta_ms": None}
    if version == FillQualityRecorded.__event_schema_version__:
        return payload
    raise ValueError(f"unsupported FillQualityRecorded schema version {version}")
