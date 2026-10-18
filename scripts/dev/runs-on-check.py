#!/usr/bin/env python3
"""Fail any workflow job whose ``runs-on`` can resolve to a GitHub-hosted runner (#1069).

CI runs on server-01's self-hosted runners only (Rick, 2026-10-17; issue #754).
A job that falls back to ``ubuntu-latest`` when a runner variable is unset would
run on a machine that is not his, holding the deploy secrets and the write
token. A ``runs-on`` passes when:

- it is a list that contains ``self-hosted``; or
- it is an expression whose last ``||`` operand is ``fromJSON('[...]')`` of a
  list that contains ``self-hosted`` and which names no hosted image anywhere.

Usage:
    python3 scripts/dev/runs-on-check.py [WORKFLOW_DIR]    (default .github/workflows)
    HERMES_SELF_TEST=1 python3 scripts/dev/runs-on-check.py

Exit codes:
    0 - every job is pinned to self-hosted runners
    1 - a job can resolve to a hosted runner; one line per job on stderr
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

HOSTED_IMAGE = re.compile(r"\b(?:ubuntu|windows|macos)-(?:latest|\d)", re.IGNORECASE)
_FALLBACK = re.compile(r"fromJSON\(\s*'(\[[^']*\])'\s*\)\s*\)?\s*\}\}\s*$")


def violation(runs_on: Any) -> str | None:
    """Return why ``runs_on`` can reach a hosted runner, or ``None`` when it cannot."""
    if isinstance(runs_on, list):
        labels = [str(label) for label in runs_on]
        if any(HOSTED_IMAGE.search(label) for label in labels):
            return "names a GitHub-hosted image"
        return None if "self-hosted" in labels else "list without self-hosted"
    if not isinstance(runs_on, str):
        return "runs-on is neither a list nor an expression"
    if HOSTED_IMAGE.search(runs_on):
        return "names a GitHub-hosted image"
    if runs_on == "self-hosted":
        return None
    match = _FALLBACK.search(runs_on.strip())
    if match is None:
        return "no fixed self-hosted fallback at the end of the expression"
    try:
        labels = json.loads(match.group(1))
    except json.JSONDecodeError:
        return "the fallback is not a JSON list"
    return None if "self-hosted" in labels else "the fallback list has no self-hosted"


def check(directory: Path) -> list[str]:
    """Return one message per job in ``directory``'s workflows that can use a hosted runner."""
    problems: list[str] = []
    for path in sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = workflow.get("jobs", {}) if isinstance(workflow, dict) else {}
        for name, job in jobs.items():
            if not isinstance(job, dict) or "uses" in job:
                continue  # a reusable-workflow call runs on the callee's runner, checked there
            reason = violation(job.get("runs-on"))
            if reason is not None:
                problems.append(f"{path.name}: job '{name}': {reason}")
    return problems


def self_test() -> int:
    """Check that the rule accepts the pinned forms and rejects the hosted ones."""
    pinned = '${{ vars.R && fromJSON(vars.R) || fromJSON(\'["self-hosted","linux"]\') }}'
    cases = [
        (pinned, True),
        (["self-hosted", "hermes-backtest"], True),
        ("${{ vars.R && fromJSON(vars.R) || 'ubuntu-latest' }}", False),
        ("ubuntu-latest", False),
        ("${{ vars.R && fromJSON(vars.R) }}", False),
        (["ubuntu-22.04"], False),
        (["linux"], False),
        ("${{ matrix.os }}", False),
        ("${{ vars.R || fromJSON('[\"linux\"]') }}", False),
        ("${{ vars.R || fromJSON('[\"self-hosted\"]') && 'windows-latest' }}", False),
    ]
    failures = [str(case) for case, ok in cases if (violation(case) is None) != ok]
    if failures:
        print(f"SELF-TEST FAIL: {failures}", file=sys.stderr)
        return 1
    print(f"SELF-TEST PASS: {len(cases)} cases")
    return 0


def main(argv: list[str]) -> int:
    """Run the check over a workflow directory."""
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return self_test()
    directory = Path(argv[1]) if len(argv) > 1 else Path(".github/workflows")
    problems = check(directory)
    for line in problems:
        print(f"runs-on: {line}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
