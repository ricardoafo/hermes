"""Slack message templates — pure functions per concrete event.

Each concrete ``DomainEvent`` gets its own ``_render_<event>`` function
that produces a :class:`SlackMessage` — a NamedTuple carrying the plain
text fallback, the Block Kit blocks, and any file attachments to upload
after posting the message.

Format skeleton for SHORT events:

    header block: "━ <event title> — <ISO timestamp> ━"
    section block: 2-4 key-fact lines
    context block: "ref: <correlation-tail>"

Format skeleton for LONG events (DAILY_TRADE_DIGEST, WEEKLY_REBALANCE_REPORT,
MONTHLY_TAX_REPORT): same header + summary section PLUS one or more file
attachments (generated CSV bytes; the adapter uploads them as threaded
replies). PDF generation is deferred — MVP1 ships CSV only.

Design invariants
-----------------

- **Pure**: no I/O, no time reads (event carries its own timestamp), no
  state. Two calls with equal input return equal ``SlackMessage``
  tuples with equal-by-value attachments.
- **Application-layer**: MAY import ``hermes.domain.events`` + domain
  value objects. MUST NOT import ``hermes.adapters`` or
  ``hermes.infrastructure``.
- **No runtime-mutable registry**: :data:`_TEMPLATES` is populated at
  import time. A missing entry raises ``ValueError`` at first render.
- **Attachment generation**: CSV bytes generated via stdlib ``csv``.
  No third-party deps. PDF generation deliberately out-of-scope for
  MVP1 (reportlab / weasyprint would drag in a large native tree).

Reference: 2026-08-23-slack-alongside-telegram.md; code-conventions.md §1.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import Any, Final, NamedTuple

from hermes.application.messages._escape import correlation_tail
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.strategy_resumed import StrategyResumed
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.events.trade_rejected import TradeRejected

__all__ = ["FileAttachment", "SlackMessage", "slack_template"]

_CSV_MIME: Final[str] = "text/csv"


class FileAttachment(NamedTuple):
    """A single file to upload alongside a Slack message.

    Frozen by construction (NamedTuple). Adapters iterate the message's
    ``attachments`` tuple and upload each via the Slack v2 external-upload
    flow.

    Fields:
        name: Filename with extension (e.g. ``"digest-2026-08-23.csv"``).
        mime: MIME type (e.g. ``"text/csv"``).
        content: Raw bytes to upload. Pre-encoded; the adapter does no
            further translation.
    """

    name: str
    mime: str
    content: bytes


class SlackMessage(NamedTuple):
    """A rendered Slack post — fallback text, Block Kit blocks, and files.

    Frozen NamedTuple. The adapter posts ``text`` + ``blocks`` in one
    ``chat.postMessage`` call, then uploads every ``attachments`` entry
    as a threaded reply.

    Fields:
        text: Plain-text fallback for clients that do not render Block
            Kit. Also serves as the notification preview.
        blocks: Block Kit blocks list — dicts matching Slack's schema
            (https://api.slack.com/reference/block-kit/blocks).
        attachments: Files to upload after the message. Empty tuple for
            short events; populated for long/reporting events.
    """

    text: str
    blocks: list[dict[str, Any]]
    attachments: tuple[FileAttachment, ...]


def _header_block(title: str, iso_timestamp: str) -> dict[str, Any]:
    """Return a Block Kit ``header`` block with the standard bar formatting.

    Args:
        title: The event title (rendered inside the bar).
        iso_timestamp: ISO-8601 UTC timestamp string.

    Returns:
        A Block Kit ``header`` block dict.
    """
    text = f"\u2501 {title} \u2014 {iso_timestamp} \u2501"
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _section_block(text: str) -> dict[str, Any]:
    """Return a Block Kit ``section`` block with mrkdwn text.

    Args:
        text: The section body (Slack mrkdwn accepted).

    Returns:
        A Block Kit ``section`` block dict.
    """
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context_footer(correlation_id: str) -> dict[str, Any]:
    """Return a Block Kit ``context`` footer with ``ref: <tail>``.

    Args:
        correlation_id: Full correlation ULID; only the tail appears.

    Returns:
        A Block Kit ``context`` block dict.
    """
    tail = correlation_tail(correlation_id)
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": f"ref: `{tail}`"}]}


def _iso(event: DomainEvent) -> str:
    """Return ``event.occurred_at`` as an ISO-8601 UTC string."""
    return event.occurred_at.isoformat()


# ---------------------------------------------------------------------------
# Concrete templates
# ---------------------------------------------------------------------------


def _render_strategy_paused(event: StrategyPaused) -> SlackMessage:
    """Render a StrategyPaused as a short Slack message with no attachments."""
    title = "Strategy paused"
    text = f"{title} — {event.strategy_id} — {event.reason}"
    body = (
        f"*Strategy*: `{event.strategy_id}`\n"
        f"*Severity*: `{event.severity.value}`\n"
        f"*Reason*: {event.reason}"
    )
    blocks = [
        _header_block(title, _iso(event)),
        _section_block(body),
        _context_footer(event.correlation_id),
    ]
    return SlackMessage(text=text, blocks=blocks, attachments=())


def _render_strategy_resumed(event: StrategyResumed) -> SlackMessage:
    """Render a StrategyResumed as a short recovery notice."""
    title = "Strategy resumed"
    text = f"{title} — {event.strategy_id} — paused for {event.pause_duration_seconds}s"
    body = (
        f"*Strategy*: `{event.strategy_id}`\n*Paused for*: `{event.pause_duration_seconds}` seconds"
    )
    blocks = [
        _header_block(title, _iso(event)),
        _section_block(body),
        _context_footer(event.correlation_id),
    ]
    return SlackMessage(text=text, blocks=blocks, attachments=())


def _render_balance_low(event: BalanceLow) -> SlackMessage:
    """Render a BalanceLow as a short Slack message with no attachments.

    Routing config sends BalanceLow to Telegram only, but the template
    is kept so a Slack-only test rig or a future routing change works
    without a template gap.
    """
    title = "Balance low"
    text = (
        f"{title} — {event.asset.value} at {event.current.amount} "
        f"(threshold {event.threshold.amount})"
    )
    body = (
        f"*Asset*: `{event.asset.value}`\n"
        f"*Current*: `{event.current.amount}`\n"
        f"*Threshold*: `{event.threshold.amount}`"
    )
    blocks = [
        _header_block(title, _iso(event)),
        _section_block(body),
        _context_footer(event.correlation_id),
    ]
    return SlackMessage(text=text, blocks=blocks, attachments=())


def _render_trade_rejected(event: TradeRejected) -> SlackMessage:
    """Render a TradeRejected as a short Slack message with no attachments."""
    title = "Trade rejected"
    text = f"{title} — {event.exchange} {event.side.value} {event.pair} — {event.rejection_reason}"
    body = (
        f"*Exchange*: `{event.exchange}`\n"
        f"*Pair*: `{event.pair}`\n"
        f"*Side*: `{event.side.value}`\n"
        f"*Reason*: {event.rejection_reason}"
    )
    blocks = [
        _header_block(title, _iso(event)),
        _section_block(body),
        _context_footer(event.correlation_id),
    ]
    return SlackMessage(text=text, blocks=blocks, attachments=())


def _render_anomaly_detected(event: AnomalyDetected) -> SlackMessage:
    """Render an AnomalyDetected as a short Slack message with no attachments."""
    title = "Anomaly detected"
    text = f"{title} — [{event.source}] {event.reason}"
    body = (
        f"*Source*: `{event.source}`\n"
        f"*Severity*: `{event.severity.value}`\n"
        f"*Reason*: {event.reason}"
    )
    blocks = [
        _header_block(title, _iso(event)),
        _section_block(body),
        _context_footer(event.correlation_id),
    ]
    return SlackMessage(text=text, blocks=blocks, attachments=())


def _render_trade_executed(event: TradeExecuted) -> SlackMessage:
    """Render a TradeExecuted (as POSITION_LARGE) as a short Slack message."""
    title = "Position large"
    text = f"{title} — {event.side.value} {event.qty} {event.pair} @ {event.price.amount}"
    body = (
        f"*Pair*: `{event.pair}`\n"
        f"*Side*: `{event.side.value}`\n"
        f"*Qty*: `{event.qty}`\n"
        f"*Price*: `{event.price.amount}`"
    )
    blocks = [
        _header_block(title, _iso(event)),
        _section_block(body),
        _context_footer(event.correlation_id),
    ]
    return SlackMessage(text=text, blocks=blocks, attachments=())


def _render_rebalance_triggered(event: RebalanceTriggered) -> SlackMessage:
    """Render a RebalanceTriggered as a short Slack message with no attachments."""
    title = "Rebalance triggered"
    text = (
        f"{title} — {event.pair} at {event.current_weight.value} "
        f"vs target {event.target_weight.value}"
    )
    body = (
        f"*Pair*: `{event.pair}`\n"
        f"*Current weight*: `{event.current_weight.value}`\n"
        f"*Target weight*: `{event.target_weight.value}`\n"
        f"*Band*: `{event.band.value}`"
    )
    blocks = [
        _header_block(title, _iso(event)),
        _section_block(body),
        _context_footer(event.correlation_id),
    ]
    return SlackMessage(text=text, blocks=blocks, attachments=())


def _daily_digest_csv(event: DailyTradeDigest) -> bytes:
    """Generate the CSV attachment bytes for a DailyTradeDigest.

    Columns: ``bucket, pair, pnl_amount, pnl_currency``. Winners come
    first, then losers, both in the order supplied by the event.

    Args:
        event: The digest to serialise.

    Returns:
        UTF-8 encoded CSV bytes with a header row.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["bucket", "pair", "pnl_amount", "pnl_currency"])
    for summary in event.top_winners:
        writer.writerow(
            ["winner", str(summary.pair), str(summary.pnl.amount), summary.pnl.currency.value],
        )
    for summary in event.top_losers:
        writer.writerow(
            ["loser", str(summary.pair), str(summary.pnl.amount), summary.pnl.currency.value],
        )
    return buffer.getvalue().encode("utf-8")


def _render_daily_trade_digest(event: DailyTradeDigest) -> SlackMessage:
    """Render a DailyTradeDigest as a rich Slack message with a CSV attachment.

    Message shape: header + summary section (totals + counts) + a context
    footer. The full per-trade winners/losers live in an attached CSV.
    """
    title = f"Daily trade digest — {event.day.isoformat()}"
    text = (
        f"{title} — {event.filled}/{event.total_trades} filled, "
        f"notional {event.total_notional.amount} {event.total_notional.currency.value}"
    )
    body = (
        f"*Day*: `{event.day.isoformat()}`\n"
        f"*Total trades*: `{event.total_trades}`\n"
        f"*Filled*: `{event.filled}`\n"
        f"*Rejected*: `{event.rejected}`\n"
        f"*Total notional*: `{event.total_notional.amount} {event.total_notional.currency.value}`\n"
        f"*Top winners*: `{len(event.top_winners)}` — see attached CSV\n"
        f"*Top losers*: `{len(event.top_losers)}` — see attached CSV"
    )
    blocks = [
        _header_block(title, _iso(event)),
        _section_block(body),
        _context_footer(event.correlation_id),
    ]
    csv_bytes = _daily_digest_csv(event)
    filename = f"digest-{event.day.isoformat()}.csv"
    return SlackMessage(
        text=text,
        blocks=blocks,
        attachments=(FileAttachment(name=filename, mime=_CSV_MIME, content=csv_bytes),),
    )


# ---------------------------------------------------------------------------
# Dispatch registry — populated at import, never mutated.
# ---------------------------------------------------------------------------

_TEMPLATES: Final[dict[type, Callable[[DomainEvent], SlackMessage]]] = {
    StrategyPaused: _render_strategy_paused,  # type: ignore[dict-item]
    StrategyResumed: _render_strategy_resumed,  # type: ignore[dict-item]
    BalanceLow: _render_balance_low,  # type: ignore[dict-item]
    TradeRejected: _render_trade_rejected,  # type: ignore[dict-item]
    AnomalyDetected: _render_anomaly_detected,  # type: ignore[dict-item]
    TradeExecuted: _render_trade_executed,  # type: ignore[dict-item]
    RebalanceTriggered: _render_rebalance_triggered,  # type: ignore[dict-item]
    DailyTradeDigest: _render_daily_trade_digest,  # type: ignore[dict-item]
}


def slack_template(event: DomainEvent) -> SlackMessage:
    """Render a domain event as a :class:`SlackMessage`.

    Dispatches on ``type(event)`` — subclasses do NOT inherit their
    parent's template. Adding a new event class requires adding both a
    ``_render_<name>`` function AND an entry in :data:`_TEMPLATES`.

    Args:
        event: A concrete :class:`DomainEvent` instance.

    Returns:
        A :class:`SlackMessage` with plain-text fallback, Block Kit
        blocks, and optional file attachments.

    Raises:
        ValueError: If no template is registered for ``type(event)``.
            The message names the missing type so the operator can fix
            the registry.
    """
    render = _TEMPLATES.get(type(event))
    if render is None:
        raise ValueError(
            f"No Slack template registered for {type(event).__qualname__} — "
            f"add a _render function and a _TEMPLATES entry in "
            f"src/hermes/application/messages/slack.py."
        )
    return render(event)
