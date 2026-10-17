"""Shared SQL and rehydration for the ``RiskHaltRecorded`` rows of ``events`` (#749).

Serve's :class:`~hermes.adapters.ledger.sqlite_ledger.SqliteLedger` writes and
deduplicates the rows; the read-only status reader lists them. Both parse a
row through :func:`risk_halt_from_payload`, so they cannot disagree on shape.

Reference: GitHub issue #749.
"""

from datetime import datetime
from typing import Any, Final

from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["RISK_HALT_EVENT_TYPE", "risk_halt_from_payload"]

RISK_HALT_EVENT_TYPE: Final = RiskHaltRecorded.__qualname__
"""``events.event_type`` of every halt and kill row."""


def risk_halt_from_payload(payload: dict[str, Any]) -> RiskHaltRecorded:
    """Rehydrate a :class:`RiskHaltRecorded` from its stored JSON payload.

    Args:
        payload: The decoded ``payload_json`` of a ``RiskHaltRecorded`` row.

    Returns:
        The event with every stored field restored.

    Raises:
        KeyError: A field is missing from the payload.
        ValueError: A field holds a value of the wrong shape.
    """
    return RiskHaltRecorded(
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        event_seq=int(payload["event_seq"]),
        strategy_id=StrategyId(payload["strategy_id"]["value"]),
        pair=Pair(
            base=Currency(payload["pair"]["base"]),
            quote=Currency(payload["pair"]["quote"]),
        ),
        kind=HaltKind(payload["kind"]),
        rule=str(payload["rule"]),
        reason=str(payload["reason"]),
        pre_registration_ref=payload["pre_registration_ref"],
    )
