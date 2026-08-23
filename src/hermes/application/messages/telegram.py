"""Telegram message templates — pure functions per concrete event.

Each concrete ``DomainEvent`` gets its own ``_render_<event>`` function
that produces a 3-4-line MarkdownV2-escaped string suitable for a phone
push. The public ``telegram_template`` dispatches on ``type(event)``
through a static registry — a missing entry raises ``ValueError`` so
misclassification surfaces immediately instead of shipping an empty
notification.

Format skeleton (see ``docs/decisions/2026-08-23-slack-alongside-telegram.md``
§ "Extended MVP1 events" for shape rationale):

    <emoji> <EVENT_NAME_UPPER>
    <one-sentence what+when>
    ref: <correlation-tail-6> · [action: <optional-hint>]

Target: ~150 characters, 3-4 lines. Telegram truncates long messages and
truncated formatting is worse than none — templates stay terse.

Design invariants
-----------------

- **Pure**: no I/O, no time reads (event carries its own timestamp), no
  state. Two calls with equal input return equal strings.
- **Application-layer**: MAY import ``hermes.domain.events`` and the
  domain value objects. MUST NOT import ``hermes.adapters`` or
  ``hermes.infrastructure`` — import-linter enforces this.
- **MarkdownV2 escaping**: every user-controlled substring runs through
  :func:`escape_markdown_v2`. Static markdown formatting (``*bold*``,
  bracket delimiters) is applied AROUND the escaped substring so it
  renders as intended.
- **No runtime-mutable registry**: the dispatch table
  :data:`_TEMPLATES` is populated at import time and NEVER modified.
  Sub-agents that want to add a new event add a ``_render`` function
  AND a table entry in the same PR — a forgotten entry raises
  ``ValueError`` at first render, not silently.

Reference: 2026-08-23-slack-alongside-telegram.md; code-conventions.md §1.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from hermes.application.messages._escape import (
    correlation_tail,
    escape_markdown_v2,
)
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.events.trade_rejected import TradeRejected

__all__ = ["telegram_template"]


# ---------------------------------------------------------------------------
# Severity → emoji glyph. Public modules pick the emoji by intent, not by
# literal enum value — a template for a critical anomaly and a template for
# a rollback should share the "critical" glyph.
# ---------------------------------------------------------------------------

_EMOJI_CRITICAL: Final[str] = "\U0001f534"  # red circle
_EMOJI_WARNING: Final[str] = "\U0001f7e1"  # yellow circle
_EMOJI_INFO: Final[str] = "\U0001f535"  # blue circle
_EMOJI_RECOVERY: Final[str] = "\u2705"  # heavy check


def _emoji_for(severity: AnomalySeverity) -> str:
    """Return the glyph for an ``AnomalySeverity`` value.

    Args:
        severity: The severity to map.

    Returns:
        A single-character emoji glyph.
    """
    match severity:
        case AnomalySeverity.CRITICAL:
            return _EMOJI_CRITICAL
        case AnomalySeverity.WARNING:
            return _EMOJI_WARNING
        case AnomalySeverity.INFO:
            return _EMOJI_INFO


def _render_line(emoji: str, title_upper: str, body: str, tail: str, hint: str = "") -> str:
    """Assemble the standard 3-line Telegram skeleton.

    Args:
        emoji: Severity glyph.
        title_upper: The event name in UPPER_SNAKE_CASE (rendered bold).
        body: One-sentence factual summary (already escape-safe or plain).
        tail: 6-char correlation-ID tail.
        hint: Optional short action hint (rendered in brackets on the ref line).

    Returns:
        A 3-line MarkdownV2 string.
    """
    header = f"{emoji} *{escape_markdown_v2(title_upper)}*"
    body_line = escape_markdown_v2(body)
    ref_line = f"ref: {escape_markdown_v2(tail)}"
    if hint:
        ref_line = f"{ref_line} · action: {escape_markdown_v2(hint)}"
    return f"{header}\n{body_line}\n{ref_line}"


# ---------------------------------------------------------------------------
# Concrete templates — one per event type. Kept short and factual.
# ---------------------------------------------------------------------------


def _render_strategy_paused(event: StrategyPaused) -> str:
    """Render a StrategyPaused as a 3-line MarkdownV2 push."""
    emoji = _emoji_for(event.severity)
    body = f"strategy '{event.strategy_id}' paused — {event.reason}"
    return _render_line(
        emoji=emoji,
        title_upper="STRATEGY_PAUSED",
        body=body,
        tail=correlation_tail(event.correlation_id),
        hint="review strategy",
    )


def _render_balance_low(event: BalanceLow) -> str:
    """Render a BalanceLow as a 3-line MarkdownV2 push."""
    body = (
        f"{event.asset.value} balance {event.current.amount} below threshold "
        f"{event.threshold.amount}"
    )
    return _render_line(
        emoji=_EMOJI_CRITICAL,
        title_upper="BALANCE_LOW",
        body=body,
        tail=correlation_tail(event.correlation_id),
        hint="top up",
    )


def _render_trade_rejected(event: TradeRejected) -> str:
    """Render a TradeRejected as a 3-line MarkdownV2 push (escalated form)."""
    body = f"{event.exchange} rejected {event.side.value} {event.pair} — {event.rejection_reason}"
    return _render_line(
        emoji=_EMOJI_CRITICAL,
        title_upper="TRADE_REJECTED",
        body=body,
        tail=correlation_tail(event.correlation_id),
    )


def _render_anomaly_detected(event: AnomalyDetected) -> str:
    """Render an AnomalyDetected as a 3-line MarkdownV2 push."""
    emoji = _emoji_for(event.severity)
    body = f"[{event.source}] {event.reason}"
    return _render_line(
        emoji=emoji,
        title_upper="ANOMALY_DETECTED",
        body=body,
        tail=correlation_tail(event.correlation_id),
    )


def _render_trade_executed(event: TradeExecuted) -> str:
    """Render a TradeExecuted as a 3-line MarkdownV2 push (POSITION_LARGE class).

    Trade fills alone are log-only per the routing table; this renderer is
    used when a fill is escalated as ``POSITION_LARGE`` (large-position
    notice). The renderer does NOT check size — the escalation layer
    already decided the fill qualifies.
    """
    body = f"{event.side.value} {event.qty} {event.pair} @ {event.price.amount}"
    return _render_line(
        emoji=_EMOJI_INFO,
        title_upper="POSITION_LARGE",
        body=body,
        tail=correlation_tail(event.correlation_id),
    )


def _render_rebalance_triggered(event: RebalanceTriggered) -> str:
    """Render a RebalanceTriggered as a 3-line MarkdownV2 push."""
    body = (
        f"{event.pair} weight {event.current_weight.value} vs target "
        f"{event.target_weight.value} (band {event.band.value})"
    )
    return _render_line(
        emoji=_EMOJI_INFO,
        title_upper="REBALANCE_TRIGGERED",
        body=body,
        tail=correlation_tail(event.correlation_id),
    )


# ---------------------------------------------------------------------------
# Dispatch registry. Missing entries raise at first render — no silent
# empty pushes. Populated at import time; NEVER mutated at runtime.
# ---------------------------------------------------------------------------

_TEMPLATES: Final[dict[type, Callable[[DomainEvent], str]]] = {
    StrategyPaused: _render_strategy_paused,  # type: ignore[dict-item]
    BalanceLow: _render_balance_low,  # type: ignore[dict-item]
    TradeRejected: _render_trade_rejected,  # type: ignore[dict-item]
    AnomalyDetected: _render_anomaly_detected,  # type: ignore[dict-item]
    TradeExecuted: _render_trade_executed,  # type: ignore[dict-item]
    RebalanceTriggered: _render_rebalance_triggered,  # type: ignore[dict-item]
}


def telegram_template(event: DomainEvent) -> str:
    """Render a domain event as a MarkdownV2-safe Telegram message.

    Dispatches on ``type(event)`` — subclasses do NOT inherit their
    parent's template. Adding a new event class requires adding both a
    ``_render_<name>`` function AND an entry in :data:`_TEMPLATES`.

    Args:
        event: A concrete :class:`DomainEvent` instance.

    Returns:
        A short 3-line string, MarkdownV2-escaped, ready to send.

    Raises:
        ValueError: If no template is registered for ``type(event)``.
            The message names the missing type so the operator can fix
            the registry (or the caller can catch and fall back).
    """
    render = _TEMPLATES.get(type(event))
    if render is None:
        raise ValueError(
            f"No Telegram template registered for {type(event).__qualname__} — "
            f"add a _render function and a _TEMPLATES entry in "
            f"src/hermes/application/messages/telegram.py."
        )
    return render(event)
