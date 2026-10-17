"""Wait until a Textual app under test has really finished its pending work.

``Pilot.pause()`` decides the app is idle by comparing process time with wall
time. On a loaded machine (a parallel test run) the process gets little CPU,
so that heuristic can return while messages are still queued, or before a
``DataTable`` has run its idle-time column sizing. A screenshot taken then
shows a half-drawn frame: the query list's cells cut to a few characters.

:func:`settle` pauses until every node's message queue is empty and no
``DataTable`` has column widths or dimensions left to compute, then pauses
once more so the layout catches up. It never sleeps for a fixed time.
"""

from textual.app import App
from textual.pilot import Pilot
from textual.widgets import DataTable

__all__ = ["settle"]

_ATTEMPTS = 1_000


def _quiet[R](app: App[R]) -> bool:
    nodes = [app, *app.screen.walk_children(with_self=True)]
    if any(node.message_queue_size for node in nodes):
        return False
    # DataTable sizes its columns in its Idle handler; these are its pending-work flags.
    return all(
        not table._require_update_dimensions and not table._updated_cells
        for table in app.screen.query(DataTable)
    )


async def settle[R](pilot: Pilot[R]) -> None:
    """Pause until the app has no queued messages and no pending table sizing.

    Args:
        pilot: The running app's pilot.

    Raises:
        AssertionError: The app still had work after many pauses.

    """
    for _ in range(_ATTEMPTS):
        await pilot.pause()
        if _quiet(pilot.app):
            await pilot.pause()
            return
    msg = "the app never settled"
    raise AssertionError(msg)
