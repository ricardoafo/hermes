#!/usr/bin/env python3
"""Fail when a pre-commit guard is not also enforced in CI (#840).

Pre-commit hooks only run where someone installed them. Cloud writer sessions
have no pre-commit at all, so a guard that lives only in
``.pre-commit-config.yaml`` is skipped for every commit they make. That is how
weak tests from PR #810 reached main past ``no-vague-test-assertions``, after
issue #756 had already asked for that guard in CI.

Every hook id in ``.pre-commit-config.yaml`` must be in exactly one place:

- ``CI_ENFORCED``: the ``ci-checks.sh`` check (or the CI workflow step) that
  enforces the same rule over the whole tree;
- ``LOCAL_ONLY``: with the reason it needs no CI twin;
- a ``*-self-test`` hook, which the ``self-tests`` check runs in CI.

``--run-self-tests`` runs every ``*-self-test`` hook's command, so a new
self-test is picked up with no edit here. ``KNOWN_FAILING`` lists self-tests
that fail on main with the issue fixing them; one that starts passing must be
removed from the list.

Called by:
  - ``bash scripts/dev/ci-checks.sh guards`` (coverage) and ``self-tests``
  - .pre-commit-config.yaml ``hook-coverage-check-self-test`` hook

Env vars:
  HERMES_SELF_TEST  set to 1 to run built-in pass/fail fixtures and exit

Exit codes:
  0  every hook is covered (or every self-test passed)
  1  a hook is uncovered or listed twice, a listed check is not run by CI,
     a listed hook no longer exists, or a self-test failed
  2  usage error
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SELF_TEST_SUFFIX = "-self-test"
STEP_PREFIX = "step:"
BASH = shutil.which("bash") or "/bin/bash"

CI_ENFORCED: dict[str, str] = {
    "markdownlint-cli2": "markdown",
    "regenerate-guides-index": "guides-index",
    "codeowners-syntax": "codeowners",
    "ruff-check": "lint",
    "ruff-format": "format",
    "mypy": "types",
    "import-linter": "imports",
    "migration-sql-check": "guards",
    "no-naive-datetime": "guards",
    "no-vague-test-assertions": "guards",
    "test-file-adjacency": "guards",
    "no-domain-mode-check": "guards",
    "secrets-scan": "step:Secrets scan",
    "conventional-pre-commit": "step:PR title",
}

LOCAL_ONLY: dict[str, str] = {
    "trailing-whitespace": "whitespace fixer; ruff format and markdownlint enforce it in CI",
    "end-of-file-fixer": "whitespace fixer; ruff format and markdownlint enforce it in CI",
    "mixed-line-ending": "whitespace fixer; .gitattributes normalises line endings on commit",
    "check-yaml": "workflow YAML is parsed by actionlint in CI; config YAML by its loader tests",
    "check-json": "JSON fixtures are parsed by the tests that load them",
    "check-merge-conflict": "a conflict marker fails ruff, mypy or markdownlint in CI",
    "no-pr-body-at-repo-root": "a PR body committed at the root fails markdownlint MD041 in CI",
    "pr-preflight": "the local pre-push wrapper that runs these same CI checks",
}

KNOWN_FAILING: dict[str, str] = {
    "pr-preflight-self-test": "issue #836, fixed by the issue #663 PR",
}


def hooks(config: Path) -> dict[str, str]:
    """Return every hook id in ``config`` mapped to its entry command."""
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for repo in data.get("repos", []):
        for hook in repo.get("hooks", []):
            found[hook["id"]] = str(hook.get("entry", ""))
    return found


def coverage_problems(
    hook_ids: set[str],
    ci_checks: set[str],
    workflow: str,
    ci_enforced: dict[str, str],
    local_only: dict[str, str],
) -> list[str]:
    """Return one line per hook that is uncovered, listed twice, stale or unenforced."""
    problems: list[str] = []
    for hook_id in sorted(hook_ids):
        if hook_id.endswith(SELF_TEST_SUFFIX):
            continue
        places = (hook_id in ci_enforced) + (hook_id in local_only)
        if places == 0:
            problems.append(f"{hook_id}: not enforced in CI and not listed as local-only")
        elif places == 2:
            problems.append(f"{hook_id}: listed both as CI-enforced and as local-only")
    problems.extend(
        f"{hook_id}: listed here but no such pre-commit hook exists"
        for hook_id in sorted((ci_enforced.keys() | local_only.keys()) - hook_ids)
    )
    for hook_id, where in sorted(ci_enforced.items()):
        if where.startswith(STEP_PREFIX):
            step = where.removeprefix(STEP_PREFIX)
            if not re.search(rf"^\s*- name: {re.escape(step)}\s*$", workflow, re.MULTILINE):
                problems.append(f"{hook_id}: CI workflow has no step named {step!r}")
        elif where not in ci_checks:
            problems.append(f"{hook_id}: {where!r} is not a ci-checks.sh check")
        elif not re.search(rf"ci-checks\.sh {re.escape(where)}\s*$", workflow, re.MULTILINE):
            problems.append(f"{hook_id}: the CI workflow never runs ci-checks.sh {where}")
    return problems


def run_self_tests(entries: dict[str, str], known_failing: dict[str, str]) -> list[str]:
    """Run every ``*-self-test`` entry; return one line per unexpected result."""
    problems: list[str] = []
    env = os.environ.copy()
    env.pop("HERMES_SELF_TEST", None)
    for hook_id, entry in sorted(entries.items()):
        if not hook_id.endswith(SELF_TEST_SUFFIX):
            continue
        result = subprocess.run(  # noqa: S603 — the entry is the repo's own hook command
            [BASH, "-c", entry],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        passed = result.returncode == 0
        if hook_id in known_failing:
            status = "passes" if passed else "fails as expected"
            print(f"{hook_id}: {status} ({known_failing[hook_id]})")
            if passed:
                problems.append(f"{hook_id}: passes now; remove it from KNOWN_FAILING")
            continue
        print(f"{hook_id}: {'ok' if passed else 'FAILED'}")
        if not passed:
            tail = (result.stdout + result.stderr).strip().splitlines()[-5:]
            problems.append(f"{hook_id}: failed\n    " + "\n    ".join(tail))
    problems.extend(
        f"{hook_id}: in KNOWN_FAILING but no such self-test hook exists"
        for hook_id in sorted(known_failing.keys() - entries.keys())
    )
    return problems


def _ci_checks() -> set[str]:
    listed = subprocess.run(  # noqa: S603 — fixed argv
        [BASH, str(REPO_ROOT / "scripts/dev/ci-checks.sh"), "--list"],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(listed.stdout.split())


def _self_test() -> int:
    failures: list[str] = []
    workflow = "      - name: PR title\n        run: bash scripts/dev/ci-checks.sh lint\n"
    cases: list[tuple[str, list[str], int]] = [
        (
            "covered by a check CI runs",
            coverage_problems({"a"}, {"lint"}, workflow, {"a": "lint"}, {}),
            0,
        ),
        (
            "covered by a named step",
            coverage_problems({"a"}, set(), workflow, {"a": "step:PR title"}, {}),
            0,
        ),
        (
            "local-only with a reason",
            coverage_problems({"a"}, set(), workflow, {}, {"a": "why"}),
            0,
        ),
        (
            "self-test hooks need no entry",
            coverage_problems({"a-self-test"}, set(), workflow, {}, {}),
            0,
        ),
        ("uncovered hook", coverage_problems({"a"}, set(), workflow, {}, {}), 1),
        (
            "listed twice",
            coverage_problems({"a"}, {"lint"}, workflow, {"a": "lint"}, {"a": "x"}),
            1,
        ),
        ("stale entry", coverage_problems(set(), {"lint"}, workflow, {"gone": "lint"}, {}), 1),
        ("unknown check", coverage_problems({"a"}, set(), workflow, {"a": "nope"}, {}), 1),
        (
            "check CI never runs",
            coverage_problems({"a"}, {"types"}, workflow, {"a": "types"}, {}),
            1,
        ),
        ("missing step", coverage_problems({"a"}, set(), workflow, {"a": "step:Nope"}, {}), 1),
    ]
    for name, problems, want in cases:
        if len(problems) != want:
            failures.append(f"{name}: expected {want} problem(s), got {problems}")
    ok, bad = "a-self-test", "b-self-test"
    entries = {ok: "true", bad: "false", "plain": "false"}
    runs: list[tuple[str, list[str], int]] = [
        ("a failing self-test is reported", run_self_tests(entries, {}), 1),
        ("a known failure is tolerated", run_self_tests(entries, {bad: "#1"}), 0),
        (
            "a known failure that passes is reported",
            run_self_tests(entries, {ok: "#1", bad: "#1"}),
            1,
        ),
        (
            "a stale known failure is reported",
            run_self_tests({ok: "true"}, {"gone-self-test": "#1"}),
            1,
        ),
    ]
    for name, problems, want in runs:
        if len(problems) != want:
            failures.append(f"{name}: expected {want} problem(s), got {problems}")
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config.yaml"
        config.write_text(
            "repos:\n  - repo: local\n    hooks:\n      - id: x\n        entry: run x\n"
        )
        if hooks(config) != {"x": "run x"}:
            failures.append(f"hooks() misread the config: {hooks(config)}")
    for line in failures:
        print(f"SELF-TEST FAIL: {line}")
    if failures:
        return 2
    print(f"SELF-TEST PASS: hook-coverage-check.py handles {len(cases) + len(runs) + 1} fixtures.")
    return 0


def main(argv: list[str]) -> int:
    """Run the coverage check, or the self-tests with ``--run-self-tests``."""
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return _self_test()
    if argv not in ([], ["--run-self-tests"]):
        print("usage: hook-coverage-check.py [--run-self-tests]", file=sys.stderr)
        return 2
    entries = hooks(REPO_ROOT / ".pre-commit-config.yaml")
    if argv == ["--run-self-tests"]:
        problems = run_self_tests(entries, KNOWN_FAILING)
    else:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        problems = coverage_problems(set(entries), _ci_checks(), workflow, CI_ENFORCED, LOCAL_ONLY)
    for line in problems:
        print(f"FAIL: {line}")
    if problems:
        return 1
    print("hook-coverage-check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
