"""Turn a shadow execution that did not fill into a value-free ledger record.

Serve used to leave only a log line when a signal's order was refused (#708),
so the ledger could not tell a halted entry from one never signalled. Every
refusal now becomes one :class:`TradeRejected` row: the pair, the side, the
signal kind and time, the rule that refused it and a fixed reason code.

The rule reasons can quote figures (the RO-08 drawdown, a kill criterion's
P&L), and the ledger is read by blind surfaces, so the free text is dropped:
only the rule id and a code from a fixed table are kept (decision record
2026-10-03, binding rule 4).

Recording only: no halt, kill, sizing or fee rule changes (pre-registration
§11, engine bug fix).
"""

from __future__ import annotations

from typing import Final

from hermes.application.execution.kernel import Declined, Halted
from hermes.application.execution.shadow import ShadowOutcome
from hermes.application.order_router.router import value_free_rule
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["KILL_RULE", "UNCLASSIFIED", "refusal_of"]

KILL_RULE: Final[str] = "kill"
"""Rule id recorded when a pre-registration §10 kill criterion halted the entry."""

UNCLASSIFIED: Final[str] = "unclassified"
"""Reason code for a refusing rule this table does not name."""

_KILL_REASON: Final[str] = "kill_criterion"
_RISK_HALT_RULE: Final[str] = "HALT"
_DECLINE_REASONS: Final[dict[str, str]] = {
    "no-book": "no_book",
    "no-position": "no_position",
    "fill_model": "fill_model_rejected",
    "portfolio": "portfolio_refused",
    "sizing": "sizing_failed",
    "orphan-position": "max_open_positions",
    "routing": "routing_error",
    "signal": "invalid_signal",
}


def refusal_of(
    outcome: ShadowOutcome,
    signal: StrategySignal,
    *,
    pair: Pair,
    strategy_id: StrategyId,
    exchange: str,
    correlation_id: str,
) -> TradeRejected | None:
    """Return the ledger record of a refused signal, or ``None`` when it filled.

    Args:
        outcome: What the shadow executor did with the signal.
        signal: The signal that was executed.
        pair: The pair of the signal bar.
        strategy_id: The strategy bucket that emitted the signal.
        exchange: Slug of the venue the shadow simulates.
        correlation_id: The signal's processing-cycle id.

    Returns:
        A :class:`TradeRejected` timed at the signal for a ``Declined`` or
        ``Halted`` outcome; ``None`` for an executed or already-filled one.
    """
    if isinstance(outcome, Halted):
        rule, reason = KILL_RULE, _KILL_REASON
    elif isinstance(outcome, Declined):
        rule, reason = _classify(outcome)
    else:
        return None
    return TradeRejected(
        occurred_at=signal.signal_time,
        correlation_id=correlation_id,
        pair=pair,
        side=TradeSide.BUY if signal.kind is SignalKind.ENTRY else TradeSide.SELL,
        rejection_reason=reason,
        exchange=exchange,
        attempted_at=signal.signal_time,
        strategy_id=strategy_id,
        signal_kind=signal.kind,
        rule=rule,
    )


def _classify(declined: Declined) -> tuple[str, str]:
    """Return the rule id and fixed reason code of a declined signal."""
    if declined.rule == _RISK_HALT_RULE:
        rule, reason = value_free_rule(declined.reason)
        return (rule, reason) if rule != "unknown" else (_RISK_HALT_RULE, UNCLASSIFIED)
    return declined.rule, _DECLINE_REASONS.get(declined.rule, UNCLASSIFIED)
