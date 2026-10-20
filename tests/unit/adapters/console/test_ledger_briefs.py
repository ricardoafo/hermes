"""Tests for the Briefs view of the ledger read model (#720).

Invariant: the ledger read model serves the briefs of the supervisor desk it
was given, unchanged, and without a desk the Briefs view is "not available
yet" rather than an empty list that would pass for "no brief today".
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.application.console import ViewNotAvailableError
from hermes.application.console.views import BriefSection, ConsoleBrief
from tests.unit.adapters.console._ledger_builder import one_btc_position

_BRIEF = ConsoleBrief(
    "daily",
    datetime(2026, 10, 3, 12, 12, tzinfo=UTC),
    (BriefSection("Today", "Health ok.", ("health",)),),
)


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Return a ledger holding one BTC position."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    return path


def test_briefs_are_the_desks_unchanged(ledger: Path) -> None:
    """The view returns exactly what the desk published."""
    model = LedgerReadModel.open(ledger, briefs=lambda: (_BRIEF,))

    assert model.briefs() == (_BRIEF,)


def test_without_a_desk_briefs_are_not_available(ledger: Path) -> None:
    """No desk means "not available yet", never an empty list."""
    model = LedgerReadModel.open(ledger)

    with pytest.raises(ViewNotAvailableError, match="no supervisor desk"):
        model.briefs()
