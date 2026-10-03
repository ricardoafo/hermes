"""Map the status layer's report onto the console's heartbeat view (#454).

``hermes status`` (#370) computes the verdict from the ledger and systemd; the
console (#384) shows it as a :class:`Heartbeat`. :func:`heartbeat_from_report`
is the one bridge between them, so a ledger-backed console read model, the TUI
and Telegram ``/status`` show exactly the verdict ``hermes status`` prints.

Reference: openspec/changes/454-status-console/change.md.
"""

from __future__ import annotations

from typing import Final

from hermes.application.console.views import Heartbeat, HeartbeatState
from hermes.application.status import StatusReport, VerdictLevel

__all__ = ["heartbeat_from_report"]

_STATE: Final = {
    VerdictLevel.OK: HeartbeatState.OK,
    VerdictLevel.LATE: HeartbeatState.LATE,
    VerdictLevel.DOWN: HeartbeatState.DOWN,
}


def heartbeat_from_report(report: StatusReport) -> Heartbeat:
    """Return the console heartbeat for ``report``.

    - state: the report's verdict level;
    - last check: the newest processed close across streams;
    - next check: the earliest next close across streams;
    - down since: for a down verdict, the last check, the latest moment hermes
      is known to have been working (systemd does not say when it stopped).

    Streams without data (and a report with no streams, such as a missing
    ledger) fall back to the report time.

    Args:
        report: A status report from :func:`~hermes.application.status.build_report`.

    Returns:
        The heartbeat view.
    """
    lasts = [s.last_close for s in report.streams if s.last_close is not None]
    nexts = [s.next_close for s in report.streams if s.next_close is not None]
    last_check = max(lasts, default=report.generated_at)
    next_check = min(nexts, default=report.generated_at)
    state = _STATE[report.verdict.level]
    return Heartbeat(
        state=state,
        as_of=report.generated_at,
        last_check_at=last_check,
        next_check_at=next_check,
        down_since=last_check if state is HeartbeatState.DOWN else None,
    )
