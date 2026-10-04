#!/usr/bin/env python3
"""Report which CI steps dominate wall time across recent runs (#518).

Reads the last N completed runs of one workflow through the GitHub API
and prints, per step, the median, p90 and max duration and the step's
share of the job, slowest first. A separate line reports queue wait: the
time between a job being created and a runner picking it up, which is
where a saturated runner pool shows.

Usage:
    python3 scripts/dev/ci-timing.py                 # ci.yml, last 20 runs
    python3 scripts/dev/ci-timing.py --workflow merge-queue.yml --runs 50
    python3 scripts/dev/ci-timing.py --json

Read-only. Needs ``gh`` authenticated (or ``GH_TOKEN``) with read access
to Actions. Only successful and failed runs count; cancelled and skipped
ones carry no useful timing.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

DEFAULT_REPO = "ricardoafo/hermes"
COUNTED_CONCLUSIONS = frozenset({"success", "failure"})


@dataclass(frozen=True)
class StepStats:
    """Aggregated duration of one step across runs, in seconds."""

    name: str
    runs: int
    median: float
    p90: float
    max: float
    share: float


def _seconds(start: object, end: object) -> float | None:
    if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
        return None
    started = datetime.fromisoformat(start.replace("Z", "+00:00"))
    ended = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return max((ended - started).total_seconds(), 0.0)


def percentile(values: Sequence[float], fraction: float) -> float:
    """Return the nearest-rank percentile of ``values``.

    Args:
        values: Non-empty sample.
        fraction: Between 0 and 1, e.g. 0.9 for p90.

    Returns:
        The smallest value with at least ``fraction`` of the sample at or
        below it.

    Raises:
        ValueError: When ``values`` is empty.
    """
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    rank = max(math.ceil(fraction * len(ordered)), 1)
    return ordered[rank - 1]


def step_durations(jobs: Iterable[Mapping[str, object]]) -> dict[str, list[float]]:
    """Collect each step's durations from completed, counted jobs.

    Args:
        jobs: Job objects as the Actions API returns them.

    Returns:
        Step name to the list of its durations, one per job that ran it.
    """
    durations: dict[str, list[float]] = {}
    for job in jobs:
        if job.get("conclusion") not in COUNTED_CONCLUSIONS:
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, Mapping) or step.get("conclusion") == "skipped":
                continue
            seconds = _seconds(step.get("started_at"), step.get("completed_at"))
            if seconds is not None:
                durations.setdefault(str(step.get("name")), []).append(seconds)
    return durations


def queue_waits(jobs: Iterable[Mapping[str, object]]) -> list[float]:
    """Return how long each counted job waited for a runner, in seconds.

    Args:
        jobs: Job objects as the Actions API returns them.

    Returns:
        One wait per counted job that has both timestamps.
    """
    waits = []
    for job in jobs:
        if job.get("conclusion") not in COUNTED_CONCLUSIONS:
            continue
        wait = _seconds(job.get("created_at"), job.get("started_at"))
        if wait is not None:
            waits.append(wait)
    return waits


def summarize(durations: Mapping[str, Sequence[float]]) -> list[StepStats]:
    """Aggregate per-step durations, slowest median first.

    ``share`` is the step's median over the sum of all step medians, so
    the shares add up to 1 and say where a typical run spends its time.

    Args:
        durations: Step name to its durations.

    Returns:
        One :class:`StepStats` per step with at least one duration.
    """
    medians = {name: statistics.median(values) for name, values in durations.items() if values}
    total = sum(medians.values())
    stats = [
        StepStats(
            name=name,
            runs=len(durations[name]),
            median=medians[name],
            p90=percentile(durations[name], 0.9),
            max=max(durations[name]),
            share=(medians[name] / total) if total else 0.0,
        )
        for name in medians
    ]
    return sorted(stats, key=lambda s: (-s.median, s.name))


def _gh_api(path: str) -> dict[str, object]:
    out = subprocess.run(  # noqa: S603 — fixed argv; path is built from validated args
        ["gh", "api", path],  # noqa: S607 — gh on PATH is the documented requirement
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    loaded = json.loads(out)
    if not isinstance(loaded, dict):
        raise TypeError(f"unexpected API response for {path}")
    return loaded


def fetch_jobs(repo: str, workflow: str, runs: int) -> list[dict[str, object]]:
    """Fetch the jobs of the last ``runs`` completed runs of ``workflow``."""
    listing = _gh_api(
        f"repos/{repo}/actions/workflows/{workflow}/runs?status=completed&per_page={runs}"
    )
    jobs: list[dict[str, object]] = []
    runs_found = listing.get("workflow_runs")
    for run in runs_found if isinstance(runs_found, list) else []:
        if not isinstance(run, dict) or run.get("conclusion") not in COUNTED_CONCLUSIONS:
            continue
        page = _gh_api(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")
        page_jobs = page.get("jobs")
        jobs.extend(
            j for j in (page_jobs if isinstance(page_jobs, list) else []) if isinstance(j, dict)
        )
    return jobs


def render(stats: Sequence[StepStats], waits: Sequence[float]) -> str:
    """Render the report as a plain text table."""
    lines = [f"{'step':<44} {'runs':>4} {'median':>8} {'p90':>8} {'max':>8} {'share':>6}"]
    for s in stats:
        lines.append(
            f"{s.name[:44]:<44} {s.runs:>4} {s.median:>7.0f}s {s.p90:>7.0f}s "
            f"{s.max:>7.0f}s {s.share:>5.0%}"
        )
    if waits:
        lines.append(
            f"queue wait: median {statistics.median(waits):.0f}s, "
            f"p90 {percentile(waits, 0.9):.0f}s, max {max(waits):.0f}s over {len(waits)} jobs"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the timing report; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--workflow", default="ci.yml")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= 100:
        parser.error("--runs must be between 1 and 100")

    jobs = fetch_jobs(args.repo, args.workflow, args.runs)
    stats = summarize(step_durations(jobs))
    waits = queue_waits(jobs)
    if args.json:
        print(json.dumps({"steps": [asdict(s) for s in stats], "queue_wait": waits}, indent=2))
    else:
        print(render(stats, waits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
