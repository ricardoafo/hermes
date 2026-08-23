"""TradeRejected domain event — emitted when an exchange rejects an order.

Not every rejection triggers a notification. Single rejections are
log-only; repeated / escalated rejections trigger a Telegram push per
the extended 2026-08-23 decisions-journal routing table. Notification
routing is the ``notification_router``'s job — the event itself only
captures the fact.

Reference: ADR-003 §02 (exchange interactions), code-conventions.md §10 + §3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.value_objects.pair import Pair

__all__ = ["TradeRejected"]


@dataclass(frozen=True, slots=True)
class TradeRejected(DomainEvent):
    """An exchange refused to accept a trade request.

    Emitted after the exchange returns a rejection response (validation
    failure, insufficient balance, order-book conditions, exchange-side
    outage). Precedes any retry or escalation logic — subscribers must
    not assume the trade will not be retried.

    Fields:
        pair: The trading pair the rejected order targeted.
        side: BUY or SELL — same enum as ``TradeExecuted``.
        rejection_reason: Human-readable reason from the exchange or the
            adapter's normalised classification. Kept short and safe to
            surface in notifications.
        exchange: Slug identifying the exchange (``"binance"``,
            ``"kraken"``). Lowercased convention.
        attempted_at: UTC-aware datetime when the order was submitted.

    Reference: ADR-003 §02 — swing-entry rejection paths.
    """

    __event_schema_version__: ClassVar[int] = 1

    pair: Pair
    side: TradeSide
    rejection_reason: str
    exchange: str
    attempted_at: datetime
