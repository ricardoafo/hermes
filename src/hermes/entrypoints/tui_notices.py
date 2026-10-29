"""Notices the Cockpit console raises as the bar loop moves (issue #721).

A toast when a new bar has been checked ("✓ 18:00 bar checked · next
22:00"), and an optional terminal bell when hermes turns late or down. The
bell is off unless ``HERMES_CONSOLE_BEEP=on``; it rings once per change of
state, never on every poll. Both read the heartbeat only.
"""

from collections.abc import Mapping
from datetime import tzinfo
from typing import Final

from hermes.application.console.views import Heartbeat, HeartbeatState

__all__ = ["BEEP_ENV", "bar_checked_notice", "beep_enabled", "should_beep"]

BEEP_ENV: Final = "HERMES_CONSOLE_BEEP"
_BEEP_ON: Final = frozenset({"on", "1", "true", "yes"})
_ALARMS: Final = frozenset({HeartbeatState.LATE, HeartbeatState.DOWN})


def beep_enabled(env: Mapping[str, str]) -> bool:
    """Return whether the console rings the bell; only ``HERMES_CONSOLE_BEEP=on`` (any case).

    Args:
        env: Environment snapshot.

    Returns:
        ``True`` for ``on``, ``1``, ``true`` or ``yes``; ``False`` when unset.
    """
    return env.get(BEEP_ENV, "").strip().lower() in _BEEP_ON


def bar_checked_notice(previous: Heartbeat | None, current: Heartbeat, tz: tzinfo) -> str | None:
    """Return the toast for a bar checked since the last read, if there is one.

    Args:
        previous: The heartbeat of the last read; ``None`` on the first read,
            which announces nothing (the console just opened).
        current: The heartbeat just read.
        tz: Display timezone.

    Returns:
        ``✓ 18:00 bar checked · next 22:00`` when the last check moved on and
        hermes is OK, else ``None``.
    """
    if previous is None or current.state is not HeartbeatState.OK:
        return None
    if current.last_check_at <= previous.last_check_at:
        return None
    checked = f"{current.last_check_at.astimezone(tz):%H:%M}"
    following = f"{current.next_check_at.astimezone(tz):%H:%M}"
    return f"✓ {checked} bar checked · next {following}"


def should_beep(previous: Heartbeat | None, current: Heartbeat) -> bool:
    """Return whether hermes has just turned late or down (or from late to down).

    Args:
        previous: The heartbeat of the last read, or ``None`` on the first.
        current: The heartbeat just read.

    Returns:
        ``True`` once per change into an alarm state, also on the first read.
    """
    was = None if previous is None else previous.state
    return current.state in _ALARMS and current.state is not was
