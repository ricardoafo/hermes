"""Unit tests for scripts/dev/ci-flakes.py (#564).

Protects the report's invariants:
- Only runs re-attempted on the same commit and finally green count as
  flaky; a run that failed on every attempt is a real failure.
- Failing test ids come from pytest's FAILED/ERROR lines, once each, in
  the order they first appear.
- Only failed steps are reported, and the tally counts runs per test.
"""

from __future__ import annotations

import importlib.util
import sys

from hypothesis import given
from hypothesis import strategies as st

from tests.support.paths import REPO_ROOT

_SCRIPT_PATH = REPO_ROOT / "scripts" / "dev" / "ci-flakes.py"
_spec = importlib.util.spec_from_file_location("ci_flakes", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
ci_flakes = importlib.util.module_from_spec(_spec)
sys.modules["ci_flakes"] = ci_flakes
_spec.loader.exec_module(ci_flakes)


def test_only_reattempted_green_runs_are_flaky() -> None:
    """A re-run that passed is flaky; a first-try pass or a run red on every attempt is not."""
    runs = [
        {"id": 1, "run_attempt": 2, "conclusion": "success"},
        {"id": 2, "run_attempt": 1, "conclusion": "success"},
        {"id": 3, "run_attempt": 3, "conclusion": "failure"},
    ]

    assert [r["id"] for r in ci_flakes.flaky_runs(runs)] == [1]


def test_failed_tests_reads_pytest_summary_lines_once_each() -> None:
    """FAILED and ERROR node ids are collected once, in first-seen order."""
    log = (
        "2026-10-04T08:00:00Z FAILED tests/unit/a.py::test_x - AssertionError\n"
        "2026-10-04T08:00:01Z ERROR tests/unit/b.py::test_y - fixture\n"
        "2026-10-04T08:00:02Z FAILED tests/unit/a.py::test_x - AssertionError\n"
        "2026-10-04T08:00:03Z PASSED tests/unit/c.py::test_z\n"
    )

    assert ci_flakes.failed_tests(log) == ["tests/unit/a.py::test_x", "tests/unit/b.py::test_y"]


def test_failed_steps_lists_only_failures() -> None:
    """Skipped and successful steps are not reported."""
    jobs = [
        {
            "steps": [
                {"name": "Unit tests + coverage", "conclusion": "failure"},
                {"name": "lint", "conclusion": "success"},
                {"name": "Coverage floor", "conclusion": "skipped"},
            ]
        }
    ]

    assert ci_flakes.failed_steps(jobs) == ["Unit tests + coverage"]


def test_tally_counts_runs_per_test_most_frequent_first() -> None:
    """A test that flaked in two runs ranks above one that flaked once."""
    flake = ci_flakes.Flake
    flakes = [
        flake(1, "a", "x", (), ("t::one", "t::two")),
        flake(2, "b", "y", (), ("t::one",)),
    ]

    assert ci_flakes.tally(flakes) == [("t::one", 2), ("t::two", 1)]


@given(
    st.lists(st.from_regex(r"tests/[a-z]{1,8}\.py::test_[a-z]{1,8}", fullmatch=True), max_size=8)
)
def test_failed_tests_never_repeats_an_id(ids: list[str]) -> None:
    """Whatever the log repeats, each failing id is reported exactly once."""
    log = "\n".join(f"FAILED {test_id} - boom" for test_id in ids)

    result = ci_flakes.failed_tests(log)

    assert len(result) == len(set(result))
    assert set(result) == set(ids)
