#!/usr/bin/env python3
"""Fail any workflow job whose ``runs-on`` can resolve to a GitHub-hosted runner (#1069).

CI runs on server-01's self-hosted runners only (Rick, 2026-10-17; issue #754).
A job that falls back to ``ubuntu-latest`` when a runner variable is unset would
run on a machine that is not his, holding the deploy secrets and the write
token. A ``runs-on`` passes when:

- it is a list that contains ``self-hosted``; or
- it is an expression whose last ``||`` operand is ``fromJSON('[...]')`` of a
  list that contains ``self-hosted`` and which names no hosted image anywhere; or
- it is the fork route: a pull request from a fork goes to a GitHub-hosted
  image, everything else to one of the forms above, and the job reads no
  secret but the job token. A fork's code must not run on the self-hosted
  pool, and a fork's run gets a read-only token and no secrets.

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
_FORK_ROUTE = re.compile(
    r"^\$\{\{\s*github\.event_name == 'pull_request'"
    r" && github\.event\.pull_request\.head\.repo\.full_name != github\.repository"
    r" && '(?P<image>ubuntu-\d+\.\d+)' \|\| \((?P<rest>.+)\)\s*\}\}$"
)
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_SECRET = re.compile(r"\bsecrets\b(?:\s*\.\s*(\w+)|\s*\[\s*['\"](\w+)['\"]\s*\])?")


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


def fork_route_violation(runs_on: Any, job: dict[str, Any]) -> str | None:
    """Return why a fork-routed ``runs_on`` is unsafe, or ``None`` when it is safe.

    Only called for an expression that names a hosted image. The image must be
    reached through the exact fork condition, the other branch must pass
    :func:`violation` on its own, and the job may read no secret but the job token.
    """
    match = _FORK_ROUTE.match(runs_on.strip()) if isinstance(runs_on, str) else None
    if match is None:
        return "names a GitHub-hosted image"
    inner = violation("${{ " + match.group("rest") + " }}")
    if inner is not None:
        return f"fork route: the non-fork branch {inner}"
    secrets = sorted(_secrets_read(job) - {"GITHUB_TOKEN"})
    if secrets:
        return f"fork route: the job reads secrets a fork's run must not hold: {', '.join(secrets)}"
    return None


def _strings(node: Any) -> list[str]:
    """Return every key and scalar in ``node``, depth first."""
    if isinstance(node, dict):
        return [text for key, value in node.items() for text in (str(key), *_strings(value))]
    if isinstance(node, list):
        return [text for item in node for text in _strings(item)]
    return [str(node)]


def _secrets_read(job: dict[str, Any]) -> set[str]:
    """Return the secrets ``job`` reads; ``*`` stands for the whole context.

    Covers ``secrets.X``, ``secrets['X']``, a bare ``secrets`` (as in
    ``toJSON(secrets)``) inside an expression, and ``secrets: inherit``.
    """
    found = {"*"} if job.get("secrets") is not None else set()
    for text in _strings(job):
        for expression in _EXPRESSION.findall(text):
            found |= {dot or bracket or "*" for dot, bracket in _SECRET.findall(expression)}
    return found


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
            if reason == "names a GitHub-hosted image":
                reason = fork_route_violation(job.get("runs-on"), job)
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
    fork = (
        "${{ github.event_name == 'pull_request'"
        " && github.event.pull_request.head.repo.full_name != github.repository"
        " && 'ubuntu-24.04' || (" + pinned[4:-3] + ") }}"
    )
    fork_cases = [
        (fork, {}, True),
        (fork, {"env": {"T": "${{ secrets.GITHUB_TOKEN }}"}}, True),
        (fork, {"env": {"K": "${{ secrets.VPS_SSH_KEY }}"}}, False),
        (fork, {"env": {"K": "${{ secrets['VPS_SSH_KEY'] }}"}}, False),
        (fork, {"env": {"K": "${{ toJSON(secrets) }}"}}, False),
        (fork, {"uses": "./.github/workflows/w.yml", "secrets": "inherit"}, False),
        (fork, {"name": "Holds no secrets"}, True),
        (fork.replace("!=", "=="), {}, False),
        (fork.replace('"self-hosted",', ""), {}, False),
    ]
    failures = [str(case) for case, ok in cases if (violation(case) is None) != ok]
    failures += [
        str(case)
        for case, job, ok in fork_cases
        if ((violation(case) is None or fork_route_violation(case, job) is None) != ok)
    ]
    if failures:
        print(f"SELF-TEST FAIL: {failures}", file=sys.stderr)
        return 1
    print(f"SELF-TEST PASS: {len(cases) + len(fork_cases)} cases")
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
