"""Notifier adapters — concrete implementations of ``NotifierPort``.

Per the 2026-08-23 decisions-journal entry, two channels ship in Phase 1:

- :class:`TelegramAdapter` — urgent/terse push-to-phone.
- :class:`SlackAdapter` — rich/long-form reports.
"""

from hermes.adapters.notifier.slack_adapter import (
    SlackAdapter,
    SlackConfig,
    SlackNotifierError,
)
from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)

__all__ = [
    "SlackAdapter",
    "SlackConfig",
    "SlackNotifierError",
    "TelegramAdapter",
    "TelegramConfig",
    "TelegramNotifierError",
]
