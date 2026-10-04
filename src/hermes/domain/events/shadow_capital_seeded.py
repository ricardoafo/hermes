"""ShadowCapitalSeeded domain event — the shadow run's start capital entered the ledger.

Written once per strategy, when ``hermes serve`` starts on a ledger without a
seed. ``occurred_at`` is the seed date: the earliest bar, signal or trade the
ledger already holds, or the start time of a ledger with none of them, so a
restart never moves the start of the run.

Reference: ADR-016 item 4, change ``405-usdt-fifo`` D-1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["ShadowCapitalSeeded"]


@dataclass(frozen=True, slots=True)
class ShadowCapitalSeeded(DomainEvent):
    """The start capital of one strategy was recorded as a quote-currency lot.

    Fields:
        strategy_id: Strategy the capital belongs to.
        amount: The capital, in the strategy's quote currency.

    Raises:
        ValueError: On a naive ``occurred_at``, a non-positive amount or a
            negative ``event_seq``.
    """

    __event_schema_version__: ClassVar[int] = 1

    strategy_id: StrategyId
    amount: Money

    def __post_init__(self) -> None:
        """Compute the payload hash, then require an aware time and a positive amount.

        Raises:
            ValueError: On any violated invariant.
        """
        DomainEvent.__post_init__(self)
        if self.occurred_at.tzinfo is None:
            raise ValueError("ShadowCapitalSeeded.occurred_at must be UTC-aware")
        if self.amount.amount <= 0:
            raise ValueError(f"ShadowCapitalSeeded.amount must be > 0, got {self.amount}")
