"""Message shapes and the one table that picks a shape per family and channel.

A shape is how much a message says:

- ``MINIMAL``: one line, enough to know what happened.
- ``COMPACT``: headline, one detail line, one footer line. Fits a lock screen.
- ``VERBOSE``: the audit record, with UTC times and the plan spelled out.

Which shape a channel gets for a family of events is a taste decision the
Code Owner may change, so it lives in :data:`SHAPES` and nowhere else.
Swapping a family's shape is a one-line edit there plus a snapshot update.

Every event builds one :class:`Note` holding the text of each shape. The
renderer here only chooses the lines and puts the one correlation ref at the
end; the channel modules wrap those lines for their wire format.

Reference: docs/decisions/2026-10-04-notification-templates-v2.md (rules 3, 6
    and the recommendation); openspec/changes/144-notification-system/change.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from hermes.application.messages.format import SEPARATOR

__all__ = [
    "SHAPES",
    "Channel",
    "Family",
    "Note",
    "Shape",
    "render_lines",
    "render_parent_and_plan",
    "shape_for",
]


class Family(StrEnum):
    """Groups of events that share a shape.

    Members:
        TRADE_LIFECYCLE: Signal, fill, rejection.
        HEALTH_OPS: Pause, resume, anomaly, low balance.
        REPORTS: Daily digest and the rebalance heads-up.
    """

    TRADE_LIFECYCLE = "trade_lifecycle"
    HEALTH_OPS = "health_ops"
    REPORTS = "reports"


class Channel(StrEnum):
    """Where a message is delivered."""

    TELEGRAM = "telegram"
    SLACK = "slack"


class Shape(StrEnum):
    """How much a message says."""

    MINIMAL = "minimal"
    COMPACT = "compact"
    VERBOSE = "verbose"


SHAPES: Final = MappingProxyType(
    {
        (Family.TRADE_LIFECYCLE, Channel.TELEGRAM): Shape.COMPACT,
        (Family.TRADE_LIFECYCLE, Channel.SLACK): Shape.VERBOSE,
        (Family.HEALTH_OPS, Channel.TELEGRAM): Shape.COMPACT,
        (Family.HEALTH_OPS, Channel.SLACK): Shape.COMPACT,
        (Family.REPORTS, Channel.TELEGRAM): Shape.MINIMAL,
        (Family.REPORTS, Channel.SLACK): Shape.COMPACT,
    }
)
"""The shape each family gets on each channel. The only place that decides it."""


def shape_for(family: Family, channel: Channel) -> Shape:
    """Return the shape ``family`` gets on ``channel``.

    Args:
        family: The event family.
        channel: The delivery channel.

    Returns:
        The configured shape.
    """
    return SHAPES[(family, channel)]


@dataclass(frozen=True, slots=True)
class Note:
    """The text of one event in every shape, before channel wrapping.

    Fields:
        minimal: A single line, without the ref.
        compact: Headline and detail lines, without the footer.
        verbose: The same with UTC times, without the plan and the footer.
        footer: Parts of the last line (strategy, hint); the ref follows them.
        plan: The plan lines of the verbose shape (stop, distance, withheld
            actual). Empty when the event has no plan. A channel that threads
            replies posts them apart from ``verbose``; any other renders them
            right after it.
    """

    minimal: str
    compact: tuple[str, ...]
    verbose: tuple[str, ...]
    footer: tuple[str, ...] = ()
    plan: tuple[str, ...] = ()


def render_lines(note: Note, shape: Shape, ref: str) -> tuple[str, ...]:
    """Return the lines of ``note`` in ``shape``, ending with one ref.

    Args:
        note: The event's text in every shape.
        shape: Which shape to render.
        ref: The correlation-id tail, already formatted.

    Returns:
        One line for ``MINIMAL``; otherwise the shape's lines and a last line
        made of the footer parts and the ref.
    """
    if shape is Shape.MINIMAL:
        return (f"{note.minimal}{SEPARATOR}{ref}",)
    body = note.compact if shape is Shape.COMPACT else (*note.verbose, *note.plan)
    return (*body, SEPARATOR.join((*note.footer, ref)))


def render_parent_and_plan(
    note: Note, shape: Shape, ref: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split ``note`` into the parent lines and the plan lines of a thread.

    Only the verbose shape carries a plan apart from the headline. Every
    other shape, and a verbose note without a plan, comes back whole as the
    parent with an empty plan.

    Args:
        note: The event's text in every shape.
        shape: Which shape to render.
        ref: The correlation-id tail, already formatted.

    Returns:
        The parent lines (ending with the ref line) and the plan lines.
    """
    if shape is not Shape.VERBOSE or not note.plan:
        return render_lines(note, shape, ref), ()
    return (*note.verbose, SEPARATOR.join((*note.footer, ref))), note.plan
