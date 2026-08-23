"""Message templates package — pure functions per notification channel.

Two public entry points:

- :func:`telegram_template` — takes a :class:`DomainEvent`, returns a
  short MarkdownV2-escaped string suitable for a phone push.
- :func:`slack_template` — takes a :class:`DomainEvent`, returns a
  :class:`SlackMessage` NamedTuple (text + Block Kit + attachments).

Both dispatch on ``type(event)`` through a static registry. Adding a new
event class requires editing BOTH files (a ``_render`` function + an
entry in the ``_TEMPLATES`` dict) — a missing registration raises
``ValueError`` at first render, not silently at runtime.

Templates ship standalone in this PR; the Telegram and Slack adapters
consume them in follow-up PRs. This isolation lets the CLI
(``hermes preview``) exercise templates without any adapter dependency.

Reference: ``docs/decisions/2026-08-23-slack-alongside-telegram.md``
    § "Extended MVP1 events"; code-conventions.md §1.
"""

from hermes.application.messages.slack import (
    FileAttachment,
    SlackMessage,
    slack_template,
)
from hermes.application.messages.telegram import telegram_template

__all__ = ["FileAttachment", "SlackMessage", "slack_template", "telegram_template"]
