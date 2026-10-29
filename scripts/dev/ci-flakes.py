#!/usr/bin/env python3
"""List CI tests that failed and then passed on the same commit (#564).

A run whose first attempt failed and whose re-run passed, with no new
commit in between, failed on something other than the code: a flaky
test, a hung download, a runner problem. This script finds those runs
among the last N completed runs of ci.yml, reads the failed attempt's
log, and counts each failing test id, so the same flake showing up on
several PRs stands out.

Usage:
    python3 scripts/dev/ci-flakes.py               # last 100 runs
    python3 scripts/dev/ci-flakes.py --runs 50 --json

Read-only. Needs ``gh`` authenticated with read access to Actions.
"""

import argparse
import contextlib
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

DEFAULT_REPO = "ricardoafo/hermes"
_FAILED_LINE = re.compile(r"\b(?:FAILED|ERROR) (\S+::\S+)")


@dataclass(frozen=True)
class Flake:
    """One run that failed on its first attempt and passed on a re-run."""

    run_id: int
    head_sha: str
    branch: str
    failed_steps: tuple[str, ...]
    failed_tests: tuple[str, ...]


def flaky_runs(runs: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Return the runs that were re-attempted and finally passed.

    Args:
        runs: Workflow run objects as the Actions API returns them; the
            object describes the latest attempt.

    Returns:
        The runs whose latest attempt succeeded after at least one earlier
        attempt.
    """
    flaky = []
    for run in runs:
        attempt = run.get("run_attempt")
        if isinstance(attempt, int) and attempt > 1 and run.get("conclusion") == "success":
            flaky.append(run)
    return flaky


def failed_tests(log: str) -> list[str]:
    """Return the pytest node ids a log reports as FAILED or ERROR, once each.

    Args:
        log: A job log, as ``GET /actions/jobs/{id}/logs`` returns it.

    Returns:
        Test ids in first-seen order, without duplicates.
    """
    seen: dict[str, None] = {}
    for match in _FAILED_LINE.finditer(log):
        seen.setdefault(match.group(1), None)
    return list(seen)


def failed_steps(jobs: Iterable[Mapping[str, object]]) -> list[str]:
    """Return the names of the steps that failed in an attempt's jobs."""
    names: list[str] = []
    for job in jobs:
        steps = job.get("steps")
        names.extend(
            str(step.get("name"))
            for step in (steps if isinstance(steps, list) else [])
            if isinstance(step, Mapping) and step.get("conclusion") == "failure"
        )
    return names


def tally(flakes: Sequence[Flake]) -> list[tuple[str, int]]:
    """Count how many flaky runs each test failed in, most frequent first."""
    counts = Counter(test for flake in flakes for test in flake.failed_tests)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _gh(path: str) -> str:
    return subprocess.run(  # noqa: S603 — fixed argv; path is built from API ids
        ["gh", "api", path],  # noqa: S607 — gh on PATH is the documented requirement
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _gh_json(path: str) -> dict[str, object]:
    loaded = json.loads(_gh(path))
    if not isinstance(loaded, dict):
        raise TypeError(f"unexpected API response for {path}")
    return loaded


def collect(repo: str, runs: int) -> list[Flake]:
    """Fetch recent CI runs and describe each flaky one."""
    listing = _gh_json(
        f"repos/{repo}/actions/workflows/ci.yml/runs?status=completed&per_page={runs}"
    )
    found = listing.get("workflow_runs")
    flakes = []
    for run in flaky_runs(found if isinstance(found, list) else []):
        first = _gh_json(f"repos/{repo}/actions/runs/{run['id']}/attempts/1/jobs?per_page=100")
        jobs = first.get("jobs")
        job_list = [j for j in (jobs if isinstance(jobs, list) else []) if isinstance(j, dict)]
        tests: list[str] = []
        for job in job_list:
            if job.get("conclusion") == "failure":
                # Logs expire after the retention window; the failed step
                # names still tell the story.
                with contextlib.suppress(subprocess.CalledProcessError):
                    tests.extend(failed_tests(_gh(f"repos/{repo}/actions/jobs/{job['id']}/logs")))
        flakes.append(
            Flake(
                run_id=int(str(run["id"])),
                head_sha=str(run.get("head_sha", ""))[:7],
                branch=str(run.get("head_branch", "")),
                failed_steps=tuple(failed_steps(job_list)),
                failed_tests=tuple(dict.fromkeys(tests)),
            )
        )
    return flakes


def render(flakes: Sequence[Flake]) -> str:
    """Render the flaky runs and the per-test tally as plain text."""
    if not flakes:
        return "No run failed and then passed on the same commit."
    lines = [f"{len(flakes)} run(s) failed, then passed on a re-run of the same commit:"]
    for f in flakes:
        what = ", ".join(f.failed_tests) or ", ".join(f.failed_steps) or "unknown"
        lines.append(f"  run {f.run_id}  {f.head_sha}  {f.branch}: {what}")
    counted = tally(flakes)
    if counted:
        lines.append("Flaky tests, by runs affected:")
        lines.extend(f"  {count}x  {test}" for test, count in counted)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the flake report; returns the process exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= 100:
        parser.error("--runs must be between 1 and 100")

    flakes = collect(args.repo, args.runs)
    if args.json:
        print(json.dumps([asdict(f) for f in flakes], indent=2))
    else:
        print(render(flakes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
