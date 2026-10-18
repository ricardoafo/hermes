"""The console top bar's mainnet badge, version line and next-bar share (issue #833, #815).

The badge reads the same ``allow_mainnet`` flag ``hermes serve`` loads from
config: ``SHADOW · mainnet off`` while it is off, ``LIVE · mainnet ON`` in the
down colour, reversed, while it is on, and an amber ``mainnet ?`` when the
config could not be read. Nothing here hardcodes the flag; the app is handed
what the config said. A demo scenario cannot trade, so the app shows it off.

The version line names the package in its release form (``v0.2.0``)
next to the running release read from the ``current`` link.

The next-bar share is how much of the span from the last bar check to the next
one has passed, the fill of the countdown meter.

Reference: openspec/archive/833-top-bar/change.md.
"""

from datetime import datetime
from typing import Final

from rich.text import Text

from hermes.application.console import Heartbeat, HeartbeatState
from hermes.entrypoints.tui_theme import Tokens

__all__ = [
    "MAINNET_OFF",
    "MAINNET_ON",
    "MAINNET_UNKNOWN",
    "bar_elapsed_share",
    "mainnet_text",
    "version_text",
]

MAINNET_OFF: Final = "SHADOW · mainnet off"
MAINNET_ON: Final = "LIVE · mainnet ON"
MAINNET_UNKNOWN: Final = "mainnet ?"


def mainnet_text(allow_mainnet: bool | None, tokens: Tokens) -> Text:
    """Return the mainnet badge for what the config says.

    Args:
        allow_mainnet: The config's ``allow_mainnet``; ``None`` when the config
            could not be read.
        tokens: Token sheet.

    Returns:
        ``SHADOW · mainnet off`` muted; `` LIVE · mainnet ON `` bold, reversed
        on the down colour so it cannot be missed; or ``mainnet ?`` in amber.
    """
    if allow_mainnet is None:
        return Text(MAINNET_UNKNOWN, style=f"bold {tokens.warn}")
    if allow_mainnet:
        return Text(f" {MAINNET_ON} ", style=f"bold {tokens.void} on {tokens.down}")
    return Text(MAINNET_OFF, style=tokens.muted)


def version_text(label: str | None, release: str | None) -> str:
    """Return the status row's version line.

    Args:
        label: The package version in its release form (``v0.2.0``);
            ``None`` keeps the line as it was before issue #815.
        release: The running release read from the ``current`` link, or ``None``.

    Returns:
        ``v0.2.0 · 679b676``, ``v0.2.0`` without a release,
        ``release 679b676`` without a label, or empty with neither.
    """
    if label is None:
        return "" if release is None else f"release {release}"
    return label if release is None else f"{label} · {release}"


def bar_elapsed_share(heartbeat: Heartbeat | None, now: datetime) -> float | None:
    """Return the share of the time from the last bar check to the next one that has passed.

    Args:
        heartbeat: The last heartbeat read.
        now: The current time.

    Returns:
        A share in ``[0, 1]``, or ``None`` when hermes is late, down or unknown
        (the countdown then shows nothing either).
    """
    if heartbeat is None or heartbeat.state is not HeartbeatState.OK:
        return None
    span = (heartbeat.next_check_at - heartbeat.last_check_at).total_seconds()
    if span <= 0:
        return None
    passed = (now - heartbeat.last_check_at).total_seconds()
    return min(max(passed / span, 0.0), 1.0)
