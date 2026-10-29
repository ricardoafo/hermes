"""TradeExecuted domain event — emitted when an order fills on the exchange.

Carries the fact that a trade completed. The event is the single source of
truth for the ledger row (ADR-003 §00 invariant I-06: every trade write
happens in one SQLite transaction with its domain event) and for downstream
subscribers (notification router, LLM supervisor audit).

Reference: ADR-003 §02 (data flow), code-conventions.md §10 (event schema
versioning) and §3 (immutability — frozen dataclass).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["TradeExecuted", "TradeSide"]


class TradeSide(StrEnum):
    """Direction of a trade — either BUY (open lot) or SELL (consume lots).

    Reference: ADR-003 §03 — trades table `side` column.
    """

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class TradeExecuted(DomainEvent):
    """A market or limit order filled on the exchange.

    Emitted from the use-case AFTER the exchange confirms the fill, in the
    same SQLite transaction that persists the ledger row (invariant I-06).

    Fields:
        pair: The trading pair (e.g. BTC/USDT).
        side: BUY opens a tax lot; SELL consumes existing lots via FIFO.
        qty: The quantity of the base asset traded (never negative).
        price: The fill price for one unit of the base asset in quote currency.
        fee: The exchange fee charged for the fill (positive Money).
        strategy_id: The strategy bucket the fill belongs to; the ledger replay
            uses it to rebuild each strategy's portfolio (schema version 2).

    Reference: ADR-003 §02 flow 1 (rebalancer) and flow 2 (swing entry).
    """

    __event_schema_version__: ClassVar[int] = 2

    pair: Pair
    side: TradeSide
    qty: Quantity
    price: Price
    fee: Money
    strategy_id: StrategyId
