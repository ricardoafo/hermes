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
- ``LOCAL_ONLY``: with the reason it needs no CI twin.

A per-script ``*-self-test`` hook fails the check: the one ``self-tests`` hook
already runs every script's self-test (#1190).

It also fails on a script directly under ``scripts/dev/`` that nothing else in
the tree names (no hook, workflow, script or doc), so dead tooling is deleted
instead of piling up (#1189). The changelog and archived specs do not count:
they record history, not use.

``--run-self-tests`` runs, with ``HERMES_SELF_TEST=1``, every script directly
under ``scripts/dev/`` that reads that variable, so a new self-test is picked
up with no edit here. Every one must pass: a failing self-test is fixed, never
listed as expected to fail.

Called by:
  - ``bash scripts/dev/ci-checks.sh guards`` (coverage) and ``self-tests``
  - .pre-commit-config.yaml ``self-tests`` hook

Env vars:
  HERMES_SELF_TEST  set to 1 to run built-in pass/fail fixtures and exit

Exit codes:
  0  every hook is covered (or every self-test passed)
  1  a hook is uncovered or listed twice, a listed check is not run by CI,
     a listed hook no longer exists, a dev script is unreferenced, or a
     self-test failed
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
SELF_TEST_VAR = "HERMES_SELF_TEST"
STEP_PREFIX = "step:"
BASH = shutil.which("bash") or "/bin/bash"
DEV_SCRIPTS = "scripts/dev/"
HISTORY_PATHS = ("CHANGELOG.md", "changelog.d/", "openspec/archive/")

CI_ENFORCED: dict[str, str] = {
    "markdownlint-cli2": "markdown",
    "regenerate-guides-index": "guides-index",
    "codeowners-syntax": "codeowners",
    "ruff-check": "lint",
    "ruff-format": "format",
    "mypy": "types",
    "import-linter": "imports",
    "migration-sql-check": "guards",
    "openspec-layout": "guards",
    "agents-md-check": "guards",
    "footprint-check": "guards",
    "ratchets": "guards",
    "no-vague-test-assertions": "guards",
    "test-file-adjacency": "guards",
    "no-future-annotations": "guards",
    "adr-status-check": "guards",
    "no-domain-mode-check": "guards",
    "lab-notebook-hashes": "unit",
    "secrets-scan": "step:Secrets scan",
    "conventional-pre-commit": "step:PR title",
    "self-tests": "self-tests",
}

LOCAL_ONLY: dict[str, str] = {
    "trailing-whitespace": "whitespace fixer; ruff format and markdownlint enforce it in CI",
    "end-of-file-fixer": "whitespace fixer; ruff format and markdownlint enforce it in CI",
    "mixed-line-ending": "whitespace fixer; .gitattributes normalises line endings on commit",
    "check-yaml": "workflow YAML is parsed by actionlint in CI; config YAML by its loader tests",
    "check-json": "JSON fixtures are parsed by the tests that load them",
    "check-merge-conflict": "a conflict marker fails ruff, mypy or markdownlint in CI",
    "pr-preflight": "the local pre-push wrapper that runs these same CI checks",
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
            problems.append(
                f"{hook_id}: per-script self-test hook; the self-tests hook already runs it"
            )
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


def orphan_scripts(scripts: list[str], corpus: dict[str, str]) -> list[str]:
    """Return one line per script whose file name no other file in ``corpus`` mentions."""
    problems: list[str] = []
    for script in sorted(scripts):
        name = Path(script).name
        if not any(name in text for path, text in corpus.items() if path != script):
            problems.append(f"{script}: no hook, workflow, script or doc references it")
    return problems


def _tracked_corpus() -> tuple[list[str], dict[str, str]]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 — git on PATH
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = [path for path in listed.stdout.split("\0") if path]
    scripts = [p for p in paths if p.startswith(DEV_SCRIPTS) and "/" not in p[len(DEV_SCRIPTS) :]]
    corpus: dict[str, str] = {}
    for path in paths:
        file = REPO_ROOT / path
        if path.startswith(HISTORY_PATHS) or not file.is_file():
            continue
        try:
            corpus[path] = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return scripts, corpus


def self_test_commands(scripts: list[str], corpus: dict[str, str]) -> dict[str, list[str]]:
    """Return the command for each script in ``scripts`` that reads ``HERMES_SELF_TEST``."""
    commands: dict[str, list[str]] = {}
    for script in sorted(scripts):
        if SELF_TEST_VAR not in corpus.get(script, ""):
            continue
        if script.endswith(".py"):
            commands[script] = ["uv", "run", "--no-sync", "python", script]
        else:
            commands[script] = [BASH, script]
    return commands


def run_self_tests(commands: dict[str, list[str]]) -> list[str]:
    """Run every command with ``HERMES_SELF_TEST=1``; return one line per failure."""
    problems: list[str] = []
    env = os.environ | {SELF_TEST_VAR: "1"}
    for name, argv in sorted(commands.items()):
        result = subprocess.run(  # noqa: S603 — the repo's own scripts
            argv,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        passed = result.returncode == 0
        print(f"{name}: {'ok' if passed else 'FAILED'}")
        if not passed:
            tail = (result.stdout + result.stderr).strip().splitlines()[-5:]
            problems.append(f"{name}: failed\n    " + "\n    ".join(tail))
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
            "a per-script self-test hook",
            coverage_problems({"a-self-test"}, set(), workflow, {}, {}),
            1,
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
    reads_var = [BASH, "-c", f'[ "${SELF_TEST_VAR}" = 1 ]']
    runs: list[tuple[str, list[str], int]] = [
        ("a failing self-test is reported", run_self_tests({"a": ["true"], "b": ["false"]}), 1),
        ("passing self-tests report nothing", run_self_tests({"a": ["true"]}), 0),
        ("the script sees HERMES_SELF_TEST=1", run_self_tests({"a": reads_var}), 0),
    ]
    for name, problems, want in runs:
        if len(problems) != want:
            failures.append(f"{name}: expected {want} problem(s), got {problems}")
    sh, py, plain = f"{DEV_SCRIPTS}s.sh", f"{DEV_SCRIPTS}p.py", f"{DEV_SCRIPTS}n.sh"
    corpus = {sh: SELF_TEST_VAR, py: SELF_TEST_VAR, plain: "echo"}
    found = self_test_commands([sh, py, plain], corpus)
    want_found = {sh: [BASH, sh], py: ["uv", "run", "--no-sync", "python", py]}
    if found != want_found:
        failures.append(f"self_test_commands: expected {want_found}, got {found}")
    dev = f"{DEV_SCRIPTS}x.sh"
    orphans: list[tuple[str, list[str], int]] = [
        ("a referenced script", orphan_scripts([dev], {dev: "", "ci.yml": "bash x.sh"}), 0),
        ("an unreferenced script", orphan_scripts([dev], {dev: "", "ci.yml": "y.sh"}), 1),
        ("a script naming itself", orphan_scripts([dev], {dev: "x.sh"}), 1),
    ]
    for name, problems, want in orphans:
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
    total = len(cases) + len(runs) + len(orphans) + 2
    print(f"SELF-TEST PASS: hook-coverage-check.py handles {total} fixtures.")
    return 0


def main(argv: list[str]) -> int:
    """Run the coverage check, or the self-tests with ``--run-self-tests``."""
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return _self_test()
    if argv not in ([], ["--run-self-tests"]):
        print("usage: hook-coverage-check.py [--run-self-tests]", file=sys.stderr)
        return 2
    scripts, corpus = _tracked_corpus()
    if argv == ["--run-self-tests"]:
        problems = run_self_tests(self_test_commands(scripts, corpus))
    else:
        entries = hooks(REPO_ROOT / ".pre-commit-config.yaml")
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        problems = coverage_problems(set(entries), _ci_checks(), workflow, CI_ENFORCED, LOCAL_ONLY)
        problems.extend(orphan_scripts(scripts, corpus))
    for line in problems:
        print(f"FAIL: {line}")
    if problems:
        return 1
    print("hook-coverage-check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
