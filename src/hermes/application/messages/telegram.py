"""Telegram message templates.

``telegram_template`` turns a domain event into a MarkdownV2-safe string. The
facts and the shapes come from :mod:`hermes.application.messages.notes` and
:mod:`hermes.application.messages.shapes`; this module only picks the shape
the event's family gets on Telegram and escapes the lines.

Telegram has no template for the daily digest: reports live on Slack, and the
rebalance heads-up is the only report that reaches Telegram, as one line.

Design invariants
-----------------

- **Pure**: no I/O, no time reads (the event carries its own timestamp), no
  state. Two calls with equal input return equal strings.
- **Application-layer**: imports domain events and sibling message modules,
  never ``hermes.adapters`` or ``hermes.infrastructure``.
- **MarkdownV2 escaping**: every line goes through :func:`escape_markdown_v2`,
  so a reserved character in a reason or a strategy id renders literally.
- **Blind**: no P&L word reaches a line (see ``notes``).

Reference: docs/decisions/2026-10-04-notification-templates-v2.md;
    openspec/archive/144-notification-system/change.md.
"""

from decimal import Decimal
from typing import Final

from hermes.application.messages._escape import escape_markdown_v2
from hermes.application.messages.format import fmt_ref
from hermes.application.messages.notes import build_note
from hermes.application.messages.reporting_phase import DEFAULT_REPORTING_PHASE
from hermes.application.messages.shapes import Channel, render_lines, shape_for
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest

__all__ = ["telegram_template"]

_SLACK_ONLY: Final[tuple[type[DomainEvent], ...]] = (DailyTradeDigest,)


def telegram_template(event: DomainEvent, *, eur_rate: Decimal | None = None) -> str:
    """Render a domain event as a MarkdownV2-safe Telegram message.

    Args:
        event: A concrete :class:`DomainEvent` instance.
        eur_rate: Quote units per euro for the event's pair, or ``None`` to
            keep prices in the quote currency.

    Returns:
        The message lines joined by newlines, escaped and ending with one
        correlation ref.

    Raises:
        ValueError: If Telegram has no template for ``type(event)``. The
            message names the missing type so the operator can fix the
            registry (or the caller can catch and fall back).
    """
    if isinstance(event, _SLACK_ONLY):
        raise ValueError(
            f"No Telegram template registered for {type(event).__qualname__} - "
            f"reports are Slack-only."
        )
    family, note = build_note(event, DEFAULT_REPORTING_PHASE, eur_rate)
    shape = shape_for(family, Channel.TELEGRAM)
    lines = render_lines(note, shape, fmt_ref(event.correlation_id))
    return "\n".join(escape_markdown_v2(line) for line in lines)
