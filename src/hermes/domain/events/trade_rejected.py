"""TradeRejected domain event — an order or a signal's order was refused.

Two producers. An exchange adapter emits it when a venue rejects a submitted
order. ``hermes serve`` emits it, from schema v2, when the shadow execution of
a strategy signal does not fill: a risk halt, the kill breaker, the fill
model, sizing, the position cap or a missing book refused it (#708). Those
rows carry the signal kind, the rule id and a fixed reason code, so the ledger
keeps the refusal without quoting a figure (decision record 2026-10-03,
binding rule 4).

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
from typing import Any, ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["TradeRejected", "upcast_trade_rejected_payload"]


@dataclass(frozen=True, slots=True)
class TradeRejected(DomainEvent):
    """A trade request was refused before it filled.

    Emitted after the exchange returns a rejection response (validation
    failure, insufficient balance, order-book conditions, exchange-side
    outage), or after the shadow execution of a strategy signal refuses it.
    Precedes any retry or escalation logic — subscribers must not assume
    the trade will not be retried.

    Fields:
        pair: The trading pair the rejected order targeted.
        side: BUY or SELL — same enum as ``TradeExecuted``.
        rejection_reason: Human-readable reason from the exchange or the
            adapter's normalised classification. Kept short and safe to
            surface in notifications. A refused signal carries a fixed code
            such as ``macro_volatility`` or ``no_book``, never a figure.
        exchange: Slug identifying the exchange (``"binance"``,
            ``"kraken"``). Lowercased convention.
        attempted_at: UTC-aware datetime when the order was submitted; for a
            refused signal, the signal time (the signal bar's close).
        strategy_id: Strategy bucket of the refused signal. ``None`` for an
            exchange rejection and on schema v1 rows.
        signal_kind: ``entry`` or ``exit`` of the refused signal. ``None``
            for an exchange rejection and on schema v1 rows.
        rule: Identifier of what refused the signal (``RO-07``, ``kill``,
            ``no-book``, ``fill_model``, ...). ``None`` for an exchange
            rejection and on schema v1 rows.

    Reference: ADR-003 §02 — swing-entry rejection paths.
    """

    __event_schema_version__: ClassVar[int] = 2

    pair: Pair
    side: TradeSide
    rejection_reason: str
    exchange: str
    attempted_at: datetime
    strategy_id: StrategyId | None = None
    signal_kind: SignalKind | None = None
    rule: str | None = None


def upcast_trade_rejected_payload(version: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Lift a stored ``TradeRejected`` payload to the current schema version.

    Args:
        version: ``event_schema_version`` the row was written with.
        payload: Decoded ``payload_json`` of the row.

    Returns:
        A payload carrying every field of schema v2; v1 rows get
        ``strategy_id``, ``signal_kind`` and ``rule`` as ``None``.

    Raises:
        ValueError: ``version`` is not a known schema version.
    """
    if version == 1:
        return {**payload, "strategy_id": None, "signal_kind": None, "rule": None}
    if version == TradeRejected.__event_schema_version__:
        return payload
    raise ValueError(f"unsupported TradeRejected schema version {version}")
