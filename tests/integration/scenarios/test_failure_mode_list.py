"""No known way for Hermes to fail may be left without a test, an issue or a reason (issue #839).

The catalogue in ``failure_modes.py`` lists every way we know Hermes can fail.
These tests keep it honest: every entry has exactly one answer, a gap names
its open issue, an accepted risk carries the maintainer's approval, and a named proof is
a test that exists.
"""

import re

import pytest

from tests.integration.scenarios.failure_modes import AREAS, CATALOGUE, FailureMode
from tests.support.node_ids import names_a_test

pytestmark = pytest.mark.integration

_ISSUE = re.compile(r"^issue #\d+: \S")
_APPROVED = re.compile(r"^approved by the maintainer \d{4}-\d{2}-\d{2}: \S")


def _all_modes() -> list[FailureMode]:
    return [mode for listed in CATALOGUE.values() for mode in listed]


def test_every_area_has_its_failure_modes_listed() -> None:
    """The catalogue covers each area issue #839 names, and no area is left empty."""
    assert (list(CATALOGUE), [area for area, modes in CATALOGUE.items() if not modes]) == (
        list(AREAS),
        [],
    )


def test_every_failure_mode_has_exactly_one_kind_of_answer() -> None:
    """A failure mode is proved by tests, a gap with an issue, or accepted by the maintainer."""
    wrong = [
        mode.name
        for mode in _all_modes()
        if [bool(mode.proved_by), bool(mode.gap), bool(mode.accepted)].count(True) != 1
        or (mode.gap and not _ISSUE.match(mode.gap))
        or (mode.accepted and not _APPROVED.match(mode.accepted))
    ]
    assert wrong == []


def test_every_failure_mode_is_listed_once() -> None:
    """Two entries with one name would hide which of them a reader means."""
    names = [mode.name for mode in _all_modes()]
    assert sorted({name for name in names if names.count(name) > 1}) == []


def test_every_named_proof_is_a_test_that_exists() -> None:
    """A renamed or deleted test cannot leave a failure mode pointing at nothing."""
    missing = [node for mode in _all_modes() for node in mode.proved_by if not names_a_test(node)]
    assert missing == []
