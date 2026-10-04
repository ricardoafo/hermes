"""FillQualityRecorded — how one simulated shadow fill compares with the §6.2 model.

``hermes serve`` writes one of these next to every ``TradeExecuted`` it
records, under the same ``correlation_id``. The rows are the per-fill input of
the SHADOW_CERTIFIED gate (pre-registration §7: sim-vs-live drift below 30 bps
at p50 over 90 days) and of risk rule RO-03 (last single-fill slippage).

The event holds execution quality only: prices of one order, never equity,
P&L or position value.

Reference: openspec/changes/353-fill-kernel-in-serve/change.md D-2, D-4;
code-conventions.md §10 (event schema versioning).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["BookSource", "FillQualityRecorded"]


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

    Raises:
        ValueError: On a naive time, a non-positive price or quantity, or a
            drift (slippage) without its model price (mid).
    """

    __event_schema_version__: ClassVar[int] = 1

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
