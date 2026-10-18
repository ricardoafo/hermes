#!/usr/bin/env python3
"""Verify that recently-merged PRs' linked issues are actually closed.

Audit tool that correlates three data sources for the last N merged PRs
(or the last M closed since a date), reporting one line per PR:

  1. `gh pr list --state merged` — the PR's own body (Closes #N regex).
  2. `gh issue view <N> --json state` — the linked issue's current state.
  3. `gh run view <merge-queue-run> --log` — the belt-and-suspenders
     diagnostic markers (`closed linked issue #N` / `failed to close`).

Diagnostics reported per PR:

  v3_ok                              — every linked issue CLOSED (happy path)
  v3_ok_race_condition               — issue OPEN briefly, but log shows close succeeded
  v3_fired_but_token_or_perm_bug     — log shows `failed to close` (v3 bug)
  v3_did_not_run                     — log has neither marker (pre-v3 or wrong code path)
  issue_still_open                   — issue OPEN, no log run found (dispatch failure)
  no_linked_issues                   — PR body has no Closes clause (docs-only etc.)

Never modifies state (report-only). Exit 0 iff every audited PR is
`v3_ok`, `v3_ok_race_condition`, or `no_linked_issues`; exit 1 on any
straggler.

Usage:
    python scripts/dev/verify_auto_close.py                       # last 10
    python scripts/dev/verify_auto_close.py --limit 25
    python scripts/dev/verify_auto_close.py --since 2026-08-25
    python scripts/dev/verify_auto_close.py --json                # machine-readable
    python scripts/dev/verify_auto_close.py --quiet               # exit-code only
    HERMES_SELF_TEST=1 python scripts/dev/verify_auto_close.py    # fixture matrix

Related:
    openspec/archive/verify-auto-close-tool/change.md
    .github/workflows/merge-queue.yml (v3 belt-and-suspenders block)
    #232 (v1) → #247 (v2) → #261 (v3) — the auto-close saga
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any

CLOSING_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)\b",
    re.IGNORECASE,
)


def _extract_linked(body: str) -> list[int]:
    """Extract unique linked-issue numbers from a PR body."""
    return sorted({int(m) for m in CLOSING_RE.findall(body or "")})


def _run_gh(*args: str, check: bool = True) -> str:
    """Run `gh` and return stdout. Raises on non-zero exit unless check=False."""
    if shutil.which("gh") is None:
        raise RuntimeError("gh CLI not on PATH — install from https://cli.github.com")
    gh_path = shutil.which("gh") or "gh"
    result = subprocess.run(  # noqa: S603 — gh_path is resolved via shutil.which
        [gh_path, *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def _find_merge_queue_run_id(
    runs: list[dict[str, Any]],
    pr_num: int,
    jobs_for_run: Callable[[int], list[dict[str, Any]]],
) -> int | None:
    """Return the run id whose jobs include one that processed this PR.

    Merge-queue runs share display_title="Merge queue" AND their head_sha
    is the PRE-merge main tip (not pr.mergeCommit.oid) — the run performs
    the merge, then pushes, so its head_sha never equals the resulting
    merge commit. The reliable correlation is inside the run's jobs: the
    Revalidate job is named `Revalidate (N, <sha>)` where N is the PR
    number. Iterate recent runs and query jobs until we find a match.
    """
    pattern = re.compile(rf"^Revalidate\s*\(\s*{pr_num}\s*,")
    for r in runs:
        run_id = r.get("id")
        if run_id is None:
            continue
        try:
            for job in jobs_for_run(run_id):
                if pattern.match(job.get("name", "")):
                    return run_id
        except RuntimeError:
            continue
    return None


def _jobs_for_run(run_id: int) -> list[dict[str, Any]]:
    """Fetch jobs for a merge-queue workflow run."""
    raw = _run_gh(
        "api",
        f"repos/ricardoafo/hermes/actions/runs/{run_id}/jobs?per_page=100",
        check=False,
    )
    try:
        return json.loads(raw).get("jobs", [])
    except json.JSONDecodeError:
        return []


def _self_test() -> int:
    """Run the fixture matrix — regex + report-shape checks, no live calls."""
    # Extraction from a sample body with three closing verbs.
    body = (
        "## Summary\nCloses #260. Also fixes #261. Resolved #262.\n"
        "See #999 for context (not a closing ref)."
    )
    extracted = _extract_linked(body)
    if extracted != [260, 261, 262]:
        print(f"SELF-TEST FAIL: expected [260,261,262], got {extracted}", file=sys.stderr)
        return 1

    # Extraction from a body with zero closing clauses.
    empty = "## Summary\nNo closing refs here — just prose about #999."
    extracted_empty = _extract_linked(empty)
    if extracted_empty != []:
        print(
            f"SELF-TEST FAIL: expected [] on empty body, got {extracted_empty}",
            file=sys.stderr,
        )
        return 1

    # Extraction handles case-insensitive verbs.
    mixed_case = "CLOSES #10\nfix #20\nResolves #30"
    extracted_mixed = _extract_linked(mixed_case)
    if extracted_mixed != [10, 20, 30]:
        print(
            f"SELF-TEST FAIL: case-insensitive extract broke: got {extracted_mixed}",
            file=sys.stderr,
        )
        return 1

    # Report entry shape.
    entry = {
        "pr": 42,
        "merged_at": "2026-08-26T10:00:00Z",
        "linked_issues": [{"number": 260, "state": "CLOSED"}],
        "diagnostic": "v3_ok",
    }
    required_keys = {"pr", "merged_at", "linked_issues", "diagnostic"}
    if set(entry.keys()) != required_keys:
        print(
            f"SELF-TEST FAIL: entry keys mismatch — got {set(entry.keys())}",
            file=sys.stderr,
        )
        return 1

    # Merge-queue run correlation is by job-name inside each run — the
    # PRE-merge run's head_sha is the pre-merge main tip, not
    # pr.mergeCommit.oid (the merge commit doesn't exist yet at that
    # point). The Revalidate job name embeds the PR number as
    # `Revalidate (N, <sha>)`. The fixture below uses a fake jobs_for_run
    # to assert positive match, no-match, and PR-number-substring
    # discrimination (287 must not match a job for PR 2870).
    runs_fixture = [
        {"id": 100},
        {"id": 200},
        {"id": 300},
    ]
    jobs_by_run = {
        100: [{"name": "Select"}],
        200: [{"name": "Revalidate (2870, aaaabbbb)"}, {"name": "Merge"}],
        300: [{"name": "Revalidate (287, ccccdddd)"}, {"name": "Merge"}],
    }
    matched = _find_merge_queue_run_id(runs_fixture, 287, jobs_by_run.get)
    if matched != 300:
        print(f"SELF-TEST FAIL: job-name match — expected 300, got {matched}", file=sys.stderr)
        return 1
    # PR number that never appears in any run's jobs.
    missing = _find_merge_queue_run_id(runs_fixture, 999, jobs_by_run.get)
    if missing is not None:
        print(f"SELF-TEST FAIL: no-job match — expected None, got {missing}", file=sys.stderr)
        return 1
    # Substring discrimination: PR 287 should not accidentally match
    # a run whose only job is `Revalidate (2870, ...)`.
    substring_runs = [{"id": 200}]
    substring = _find_merge_queue_run_id(substring_runs, 287, jobs_by_run.get)
    if substring is not None:
        print(
            f"SELF-TEST FAIL: substring discrimination — expected None, got {substring}",
            file=sys.stderr,
        )
        return 1

    print("SELF-TEST PASS: regex + run-matching + report-shape fixtures behave as specified.")
    return 0


def _diagnose_pr(
    pr_num: int,
    body: str,
    merged_at: str,
    *,
    log_probe: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Return (diagnostic, linked_issues) for one PR."""
    linked_nums = _extract_linked(body)
    if not linked_nums:
        return "no_linked_issues", []

    linked_issues: list[dict[str, Any]] = []
    any_open = False
    for iss in linked_nums:
        try:
            raw = _run_gh("issue", "view", str(iss), "--json", "state")
            state = json.loads(raw).get("state", "UNKNOWN")
        except RuntimeError, json.JSONDecodeError:
            state = "UNKNOWN"
        linked_issues.append({"number": iss, "state": state})
        if state != "CLOSED":
            any_open = True

    if not log_probe:
        return ("v3_ok" if not any_open else "issue_still_open"), linked_issues

    # Correlate with merge-queue workflow log — always, even when CLOSED,
    # so we can distinguish "auto-close worked" from "manually closed
    # after auto-close failed".
    try:
        raw = _run_gh(
            "api",
            "repos/ricardoafo/hermes/actions/workflows/merge-queue.yml/runs?per_page=30",
            check=False,
        )
        runs = json.loads(raw).get("workflow_runs", [])
    except RuntimeError, json.JSONDecodeError:
        runs = []

    run_id = _find_merge_queue_run_id(runs, pr_num, _jobs_for_run)

    log = ""
    if run_id is not None:
        try:
            log = _run_gh("run", "view", str(run_id), "--log", check=False)
        except RuntimeError:
            log = ""

    v3_success = "closed linked issue" in log
    v3_failure = "failed to close linked issue" in log

    if not any_open:
        # Issue is CLOSED. Distinguish auto-close from manual close.
        if v3_success:
            return "v3_ok", linked_issues
        if v3_failure:
            # Auto-close failed; someone closed it manually after.
            return "manual_close_after_v3_failure", linked_issues
        return "manual_close_no_v3_marker", linked_issues

    # Issue is OPEN. Distinguish race conditions from real bugs.
    if v3_success:
        return "v3_ok_race_condition", linked_issues
    if v3_failure:
        return "v3_fired_but_token_or_perm_bug", linked_issues
    return "v3_did_not_run", linked_issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--limit", type=int, default=10, help="Last N merged PRs")
    parser.add_argument("--since", default=None, help="ISO date filter (e.g. 2026-08-25)")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--quiet", action="store_true", help="Exit code only, no output")
    parser.add_argument(
        "--no-log-probe",
        action="store_true",
        help="Skip workflow-log correlation (faster, less detail)",
    )
    args = parser.parse_args(argv)

    if os.environ.get("HERMES_SELF_TEST") == "1":
        return _self_test()

    raw = _run_gh(
        "pr",
        "list",
        "--state",
        "merged",
        "--limit",
        str(args.limit),
        "--json",
        "number,body,mergedAt",
    )
    prs = json.loads(raw)

    report: list[dict[str, Any]] = []
    any_open = False
    audited = 0

    for pr in prs:
        merged_at = pr.get("mergedAt", "")
        if args.since and merged_at < args.since:
            continue
        pr_num = pr["number"]
        body = pr.get("body") or ""
        audited += 1

        diagnostic, linked_issues = _diagnose_pr(
            pr_num,
            body,
            merged_at,
            log_probe=not args.no_log_probe,
        )

        # Bad diagnostics — anything that means the automation did not do
        # its job cleanly. Manual closes still count as regressions here
        # (the point of the tool is to verify the AUTOMATION works, not
        # just that issues are eventually closed).
        bad_diagnostics = {
            "issue_still_open",
            "v3_fired_but_token_or_perm_bug",
            "v3_did_not_run",
            "manual_close_after_v3_failure",
            "manual_close_no_v3_marker",
        }
        if diagnostic in bad_diagnostics:
            any_open = True

        entry = {
            "pr": pr_num,
            "merged_at": merged_at,
            "linked_issues": linked_issues,
            "diagnostic": diagnostic,
        }
        report.append(entry)

        if not args.quiet and not args.json:
            n_iss = len(linked_issues)
            print(f"#{pr_num} (merged {merged_at[:10]}): {diagnostic} ({n_iss} linked)")
            for iss in linked_issues:
                print(f"    #{iss['number']} -> {iss['state']}")

    if args.json:
        print(json.dumps(report, indent=2))

    if not args.quiet and not args.json:
        print()
        print("-" * 55)
        if any_open:
            print(f"FAIL: audited {audited} PRs — at least one shows a bad diagnostic (see above)")
        else:
            print(f"PASS: audited {audited} PRs — auto-close worked on all of them")

    return 1 if any_open else 0


if __name__ == "__main__":
    sys.exit(main())
