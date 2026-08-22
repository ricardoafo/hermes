"""Notifier port — Protocol for outbound user-facing notifications.

Two channels active per the 2026-08-23 decisions-journal entry
(``docs/decisions/2026-08-23-slack-alongside-telegram.md``): Telegram
for urgent/terse, Slack for rich/reports. The application-layer router
at ``hermes.application.notification_router`` (Phase 1) decides which
adapter(s) fire per event class.

Adapters implement whichever methods they meaningfully support:

- ``TelegramAdapter``: ``notify_urgent`` (real), ``notify_report`` (no-op)
- ``SlackAdapter``: ``notify_urgent`` (real), ``notify_report`` (real, rich)

Both live under ``src/hermes/adapters/notifier/`` in Phase 1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["NotifierPort"]


@runtime_checkable
class NotifierPort(Protocol):
    """Contract every notifier adapter satisfies.

    Both methods are defined but adapters no-op the ones that don't
    fit their channel's shape (e.g. Telegram no-ops ``notify_report``
    since long-form doesn't render well in a phone alert).
    """

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Push-to-phone alert.

        Short, actionable, includes correlation-ID tail (last 6 chars)
        so an operator reading a Telegram ping can immediately grep
        journald for the full log context.
        """
        ...

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        """Rich long-form report.

        May include formatting, threads, attached artifacts (wheels,
        SBOMs, CSV reports). Delivered to a lower-urgency channel where
        the operator reads when convenient.
        """
        ...
