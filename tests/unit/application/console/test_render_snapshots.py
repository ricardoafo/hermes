"""Snapshots of every console screen, per scenario, at 80 and 120 columns.

The snapshots are the reviewed rendering of the approved console UX. A change
to any screen shows up as a snapshot diff that needs ``--snapshot-update`` and
a human look; the behaviour rules themselves are pinned in ``test_render.py``.
Snapshots store one string per line so blank lines survive the repository's
trailing-whitespace hook.
"""

from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from hermes.application.console import Surface
from tests.unit.application.console._screens import SCENARIOS, SCREENS, WIDTHS, render


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_operator_screen_matches_snapshot(
    scenario: str, screen: str, width: int, snapshot: SnapshotAssertion
) -> None:
    """The operator rendering of each screen matches its reviewed snapshot."""
    assert (
        render(scenario, screen, surface=Surface.OPERATOR_CLI, width=width).splitlines() == snapshot
    )


@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_blind_screen_matches_snapshot(
    scenario: str, screen: str, snapshot: SnapshotAssertion
) -> None:
    """The blind rendering of each screen matches its reviewed snapshot."""
    assert (
        render(scenario, screen, surface=Surface.TELEGRAM_PUSHED, width=80).splitlines() == snapshot
    )
