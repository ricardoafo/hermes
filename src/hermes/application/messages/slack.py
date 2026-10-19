"""Slack message templates.

``slack_template`` turns a domain event into a :class:`SlackMessage`: the
plain-text fallback, the Block Kit blocks and any file attachments to upload
after posting. The facts and the shapes come from
:mod:`hermes.application.messages.notes` and
:mod:`hermes.application.messages.shapes`; this module picks the shape the
event's family gets on Slack and wraps the lines in blocks.

A trade event on Slack posts its headline lines as the parent message and the
plan (stop, distance, ATR multiple, the withheld actual) as a reply in the
thread, so the channel view stays one line per trade.

Block layout: the headline is bold in one section block together with the
detail lines, and the last line (strategy, hint, correlation ref) sits in a
context block. A one-line shape is a single section block.

The daily digest is the long report: once the phase is ``CERTIFIED`` its
winners and losers go in an attached CSV, uploaded as a threaded reply.
Before certification no CSV is built and no outcome figure appears.

Design invariants
-----------------

- **Pure**: no I/O, no time reads (the event carries its own timestamp), no
  state. Two calls with equal input return equal ``SlackMessage`` tuples.
- **Application-layer**: imports domain events and sibling message modules,
  never ``hermes.adapters`` or ``hermes.infrastructure``.
- **Aggregate blind**: only :class:`~hermes.domain.events.daily_trade_digest.DailyTradeDigest`
  reads the :class:`ReportingPhase`; it renders blind unless the caller
  passes ``CERTIFIED``.

Reference: docs/decisions/2026-10-04-notification-templates-v2.md;
    openspec/archive/144-notification-system/change.md.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any, Final, NamedTuple

from hermes.application.messages._escape import escape_slack
from hermes.application.messages.format import SEPARATOR, fmt_ref
from hermes.application.messages.notes import build_note
from hermes.application.messages.reporting_phase import (
    DEFAULT_REPORTING_PHASE,
    ReportingPhase,
)
from hermes.application.messages.shapes import Channel, render_parent_and_plan, shape_for
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest

__all__ = ["FileAttachment", "SlackMessage", "SlackReply", "slack_template"]

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


class SlackReply(NamedTuple):
    """The thread reply of a message: the plan, posted under the headline.

    Fields:
        text: Plain-text fallback for clients that do not render Block Kit.
        blocks: Block Kit blocks of the reply.
    """

    text: str
    blocks: list[dict[str, Any]]


class SlackMessage(NamedTuple):
    """A rendered Slack post: fallback text, Block Kit blocks, and files.

    Frozen NamedTuple. The adapter posts ``text`` + ``blocks`` in one
    ``chat.postMessage`` call, then posts ``plan`` (when there is one) and
    uploads every ``attachments`` entry as threaded replies.

    Fields:
        text: Plain-text fallback for clients that do not render Block
            Kit. Also serves as the notification preview.
        blocks: Block Kit blocks list, dicts matching Slack's schema
            (https://api.slack.com/reference/block-kit/blocks).
        attachments: Files to upload after the message. Empty tuple for
            short events; populated for the certified daily digest.
        plan: The plan block to post as a reply in the parent's thread.
            ``None`` for events that have no plan: one message, no thread.
    """

    text: str
    blocks: list[dict[str, Any]]
    attachments: tuple[FileAttachment, ...]
    plan: SlackReply | None = None


def _section_block(text: str) -> dict[str, Any]:
    """Return a Block Kit ``section`` block carrying ``text`` as mrkdwn."""
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context_block(text: str) -> dict[str, Any]:
    """Return a Block Kit ``context`` block carrying ``text`` as mrkdwn."""
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _blocks(lines: tuple[str, ...]) -> list[dict[str, Any]]:
    """Wrap rendered lines: bold headline and details, then a context footer."""
    safe = [escape_slack(line) for line in lines]
    safe[0] = f"*{safe[0]}*"
    if len(safe) == 1:
        return [_section_block(safe[0])]
    return [_section_block("\n".join(safe[:-1])), _context_block(safe[-1])]


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


def slack_template(
    event: DomainEvent,
    *,
    phase: ReportingPhase = DEFAULT_REPORTING_PHASE,
    eur_rate: Decimal | None = None,
) -> SlackMessage:
    """Render a domain event as a :class:`SlackMessage`.

    Args:
        event: A concrete :class:`DomainEvent` instance.
        phase: Reporting phase. Defaults to pre-certification, so outcome
            figures stay withheld unless the caller unblinds explicitly.
        eur_rate: Quote units per euro for the event's pair, or ``None`` to
            keep prices in the quote currency.

    Returns:
        A :class:`SlackMessage` with plain-text fallback, Block Kit
        blocks, and optional file attachments.

    Raises:
        ValueError: If Slack has no template for ``type(event)``. The
            message names the missing type so the operator can fix the
            registry.
    """
    family, note = build_note(event, phase, eur_rate)
    shape = shape_for(family, Channel.SLACK)
    lines, plan_lines = render_parent_and_plan(note, shape, fmt_ref(event.correlation_id))
    attachments: tuple[FileAttachment, ...] = ()
    if isinstance(event, DailyTradeDigest) and phase.shows_outcomes:
        name = f"digest-{event.day.isoformat()}.csv"
        attachments = (FileAttachment(name=name, mime=_CSV_MIME, content=_daily_digest_csv(event)),)
    text = SEPARATOR.join(lines[:-1]) if len(lines) > 1 else lines[0]
    plan = None
    if plan_lines:
        plan_text = "\n".join(escape_slack(line) for line in plan_lines)
        plan = SlackReply(text=SEPARATOR.join(plan_lines), blocks=[_section_block(plan_text)])
    return SlackMessage(text=text, blocks=_blocks(lines), attachments=attachments, plan=plan)
