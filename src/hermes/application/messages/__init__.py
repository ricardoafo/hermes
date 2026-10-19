"""Message templates package — pure functions per notification channel.

Two public entry points:

- :func:`telegram_template` — takes a :class:`DomainEvent`, returns a
  short MarkdownV2-escaped string suitable for a phone push.
- :func:`slack_template` — takes a :class:`DomainEvent`, returns a
  :class:`SlackMessage` NamedTuple (text + Block Kit + attachments).

Both build their text from one channel-neutral note per event
(:mod:`~hermes.application.messages.notes`), written with the shared
formatters (:mod:`~hermes.application.messages.format`) and rendered in the
shape that :data:`~hermes.application.messages.shapes.SHAPES` assigns to the
event's family on that channel. An event with no builder raises
``ValueError`` at first render, not silently at runtime.

The adapters consume the templates; this isolation lets the CLI
(``hermes preview``) exercise them without any adapter dependency.

Reference: ``docs/decisions/2026-08-23-slack-alongside-telegram.md``
    § "Extended MVP1 events"; code-conventions.md §1.
"""

from hermes.application.messages._escape import correlation_tail
from hermes.application.messages.reporting_phase import ReportingPhase
from hermes.application.messages.slack import (
    FileAttachment,
    SlackMessage,
    slack_template,
)
from hermes.application.messages.telegram import telegram_template

__all__ = [
    "FileAttachment",
    "ReportingPhase",
    "SlackMessage",
    "correlation_tail",
    "slack_template",
    "telegram_template",
]
