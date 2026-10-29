"""Change-only heartbeat alerts for Telegram (ADR-015 D-07, D-08).

The bot feeds the ``hermes status`` verdict to :class:`HeartbeatAlerter` once a
minute; it returns the messages to push:

- OK: nothing.
- late: one silent message when a check first becomes late.
- down: one loud message, then reminders 15 min, 1 h and 4 h after it went
  down, then every 12 h while it stays down.
- back to OK after late or down: one silent "back after N min".

Quiet hours (23:00 to 08:00 Madrid by default) hold reminders until they end;
the first down message still rings. Pushed messages never carry money.

Reference: openspec/archive/379-telegram-bot/change.md (D-3).
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo
from typing import Final

from hermes.application.status import MADRID, VerdictLevel

__all__ = ["HeartbeatAlerter", "Outbound"]

_FIRST_REMINDERS: Final = (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=4))
_LATER_REMINDER: Final = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class Outbound:
    """One message to push.

    Attributes:
        text: Plain text, no money.
        silent: Send with ``disable_notification``.
    """

    text: str
    silent: bool


@dataclass
class HeartbeatAlerter:
    """Turn a stream of verdicts into change-only pushes.

    Attributes:
        tz: Timezone for quiet hours and clock times in messages.
        quiet_start: Start of quiet hours, local time.
        quiet_end: End of quiet hours, local time.
    """

    tz: tzinfo = MADRID
    quiet_start: time = time(23, 0)
    quiet_end: time = time(8, 0)
    _level: VerdictLevel = VerdictLevel.OK
    _since: datetime | None = None
    _reminders_sent: int = 0
    _next_reminder: datetime | None = field(default=None)

    def observe(self, level: VerdictLevel, headline: str, now: datetime) -> list[Outbound]:
        """Record the verdict at ``now`` and return what to push.

        Args:
            level: The current verdict level.
            headline: The verdict headline, e.g. "hermes is down".
            now: The current instant, UTC-aware.

        Returns:
            Zero or more messages, oldest first.
        """
        previous = self._level
        if level is previous:
            return self._reminder(headline, now) if level is VerdictLevel.DOWN else []
        self._level = level
        if level is VerdictLevel.OK:
            since, self._since = self._since, None
            self._next_reminder = None
            minutes = int((now - since).total_seconds() // 60) if since else 0
            return [Outbound(f"hermes is back after {minutes} min.", silent=True)]
        if level is VerdictLevel.LATE:
            if previous is VerdictLevel.OK:
                self._since = now
            self._next_reminder = None
            return [Outbound(f"{headline}. Nothing to do yet.", silent=True)]
        if self._since is None:
            self._since = now
        self._reminders_sent = 0
        self._next_reminder = now + _FIRST_REMINDERS[0]
        clock = now.astimezone(self.tz).strftime("%H:%M")
        return [
            Outbound(
                f"{headline} since {clock}. See why: hermes logs --level warning on the VPS.",
                silent=False,
            )
        ]

    def _reminder(self, headline: str, now: datetime) -> list[Outbound]:
        if self._next_reminder is None or now < self._next_reminder or self._quiet(now):
            return []
        down_since = self._since or now
        self._reminders_sent += 1
        offsets = _FIRST_REMINDERS
        if self._reminders_sent < len(offsets):
            self._next_reminder = self._next_from(down_since, offsets[self._reminders_sent], now)
        else:
            self._next_reminder = now + _LATER_REMINDER
        minutes = int((now - down_since).total_seconds() // 60)
        return [Outbound(f"Still down: {headline} for {minutes} min.", silent=False)]

    @staticmethod
    def _next_from(start: datetime, offset: timedelta, now: datetime) -> datetime:
        due = start + offset
        return due if due > now else now + timedelta(minutes=1)

    def _quiet(self, now: datetime) -> bool:
        local = now.astimezone(self.tz).time()
        if self.quiet_start <= self.quiet_end:
            return self.quiet_start <= local < self.quiet_end
        return local >= self.quiet_start or local < self.quiet_end
