"""Unit tests for scripts/dev/ci-timing.py (#518).

Protects the report's invariants:
- Only successful and failed jobs count; cancelled ones and skipped steps
  carry no useful timing and are left out.
- Steps come out slowest median first, and the shares add up to one.
- The nearest-rank percentile always returns a value from the sample,
  between its minimum and maximum.
- Queue wait is the time from job creation to a runner picking it up.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.support.paths import REPO_ROOT

_SCRIPT_PATH = REPO_ROOT / "scripts" / "dev" / "ci-timing.py"
_spec = importlib.util.spec_from_file_location("ci_timing", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
ci_timing = importlib.util.module_from_spec(_spec)
sys.modules["ci_timing"] = ci_timing
_spec.loader.exec_module(ci_timing)


def _step(name: str, start: str, end: str, conclusion: str = "success") -> dict[str, str]:
    return {"name": name, "started_at": start, "completed_at": end, "conclusion": conclusion}


def _job(conclusion: str, *steps: dict[str, str]) -> dict[str, object]:
    return {
        "conclusion": conclusion,
        "created_at": "2026-10-04T03:00:00Z",
        "started_at": "2026-10-04T03:01:30Z",
        "steps": list(steps),
    }


def test_cancelled_jobs_and_skipped_steps_are_not_counted() -> None:
    """A cancelled job or a skipped step must not drag the medians down."""
    jobs = [
        _job("success", _step("pytest", "2026-10-04T03:02:00Z", "2026-10-04T03:05:00Z")),
        _job("cancelled", _step("pytest", "2026-10-04T03:02:00Z", "2026-10-04T03:02:01Z")),
        _job(
            "failure",
            _step("pytest", "2026-10-04T03:02:00Z", "2026-10-04T03:04:00Z"),
            _step("lint", "2026-10-04T03:04:00Z", "2026-10-04T03:04:00Z", conclusion="skipped"),
        ),
    ]

    durations = ci_timing.step_durations(jobs)

    assert durations == {"pytest": [180.0, 120.0]}


def test_summary_orders_by_median_and_shares_sum_to_one() -> None:
    """The slowest step is first and the shares describe the whole job."""
    stats = ci_timing.summarize({"lint": [10.0, 20.0], "pytest": [100.0, 140.0]})

    assert [s.name for s in stats] == ["pytest", "lint"]
    assert stats[0].median == 120.0
    assert sum(s.share for s in stats) == pytest.approx(1.0)


def test_queue_wait_is_creation_to_start() -> None:
    """Queue wait measures how long a job sat before a runner took it."""
    waits = ci_timing.queue_waits([_job("success"), _job("cancelled")])

    assert waits == [90.0]


def test_percentile_of_an_empty_sample_is_an_error() -> None:
    """An empty sample has no percentile; the caller must not get a made-up number."""
    with pytest.raises(ValueError, match="empty sample"):
        ci_timing.percentile([], 0.9)


@given(
    st.lists(st.floats(min_value=0, max_value=1e6, allow_nan=False), min_size=1),
    st.floats(min_value=0, max_value=1),
)
def test_percentile_is_a_sample_value_within_the_range(
    values: list[float], fraction: float
) -> None:
    """The nearest-rank percentile is always one of the observed values."""
    result = ci_timing.percentile(values, fraction)

    assert result in values
    assert min(values) <= result <= max(values)
