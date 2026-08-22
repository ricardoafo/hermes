"""Notifier adapters — concrete implementations of ``NotifierPort``.

Per the 2026-08-23 decisions-journal entry, two channels ship in Phase 1:

- :class:`TelegramAdapter` — urgent/terse push-to-phone (this package)
- Slack adapter — rich/long-form reports (future slot)
"""

from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)

__all__ = ["TelegramAdapter", "TelegramConfig", "TelegramNotifierError"]
