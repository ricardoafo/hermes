"""Who the bot answers, and how often (ADR-015 D-04, D-08).

Only the operator's chat gets replies. Each command gets at most one reply per
5 seconds; extra requests inside that window are dropped without a reply.

Reference: openspec/archive/379-telegram-bot/change.md (D-1).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

__all__ = ["CommandGate", "GateDecision"]

_WINDOW: Final = timedelta(seconds=5)


class GateDecision(StrEnum):
    """What to do with one incoming message."""

    ANSWER = "answer"
    FOREIGN_CHAT = "foreign_chat"
    RATE_LIMITED = "rate_limited"


@dataclass
class CommandGate:
    """Allowlist plus a per-command rate limit.

    Attributes:
        allowed_chat_id: The operator's chat id, the only one answered.
        window: Minimum time between two replies to the same command.
    """

    allowed_chat_id: str
    window: timedelta = _WINDOW
    _last_reply: dict[str, datetime] = field(default_factory=dict)

    def decide(self, chat_id: str, command: str, now: datetime) -> GateDecision:
        """Decide whether to answer ``command`` from ``chat_id`` at ``now``.

        An answered command starts its window; a dropped one does not.

        Args:
            chat_id: The chat the message came from.
            command: The command name, e.g. ``status``.
            now: The current instant.

        Returns:
            The decision.
        """
        if chat_id != self.allowed_chat_id:
            return GateDecision.FOREIGN_CHAT
        last = self._last_reply.get(command)
        if last is not None and now - last < self.window:
            return GateDecision.RATE_LIMITED
        self._last_reply[command] = now
        return GateDecision.ANSWER
