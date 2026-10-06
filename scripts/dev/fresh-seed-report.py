#!/usr/bin/env python3
"""Turn the daily fresh-seed run's JUnit report into an issue body (issue #869).

The daily job runs a few hundred new seeds of the generated market paths
(``tests/integration/scenarios/test_generated_paths.py``). PR CI only ever runs
the same twenty, so a defect that needs a rarer path shows up here first. This
script lists each failing seed with what broke and how to rerun it, so the
issue it becomes is enough to reproduce and fix the defect without the run.

Invocation:

    uv run python scripts/dev/fresh-seed-report.py <junit.xml> <seeds-spec>
    HERMES_SELF_TEST=1 uv run python scripts/dev/fresh-seed-report.py

Exit codes:

- 0: every seed passed; nothing is printed
- 1: at least one seed failed; the issue body is on stdout
- 2: usage error or an unreadable report
"""

import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

_TEST = "test_every_rule_holds_after_every_bar_and_a_restart_changes_nothing"
_SEED_ID = re.compile(rf"^{_TEST}\[(?P<kind>walk|hater)-(?P<seed>\d+)\]$")
_DETAIL_LINES = 40


def failing_seeds(report: str) -> list[tuple[str, int, str]]:
    """Return ``(kind, seed, failure text)`` for each failed or errored seed in a JUnit report."""
    found = []
    for case in ET.fromstring(report).iter("testcase"):  # noqa: S314 — the job's own pytest report
        match = _SEED_ID.match(case.get("name", ""))
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if match and problem is not None:
            text = (problem.text or problem.get("message") or "").strip()
            found.append((match["kind"], int(match["seed"]), text))
    return found


def issue_body(failures: list[tuple[str, int, str]], spec: str) -> str:
    """Return the Markdown body of the issue that files these failing seeds."""
    lines = [
        "## How Hermes fails",
        "",
        f"The daily fresh-seed run (`HERMES_SCENARIO_SEEDS={spec}`) found "
        f"{len(failures)} generated market path(s) that break a rule or where a "
        "restart changes the ledger.",
        "",
    ]
    for kind, seed, text in failures:
        detail = "\n".join(text.splitlines()[-_DETAIL_LINES:])
        lines += [
            f"### `{kind}-{seed}`",
            "",
            "```text",
            detail.replace("```", "'''"),
            "```",
            "",
            "Rerun:",
            "",
            "```bash",
            f"HERMES_SCENARIO_SEEDS=1@{seed} uv run pytest "
            f"'tests/integration/scenarios/test_generated_paths.py::{_TEST}[{kind}-{seed}]'",
            "```",
            "",
        ]
    lines += [
        "## Fix",
        "",
        "Find the defect each seed shows and fix it. The same PR adds each seed to "
        "`REGRESSION_SEEDS` in `tests/integration/scenarios/test_generated_paths.py` "
        "so every PR runs it from then on. A seed never lands on main as an xfail.",
        "",
        "## Done when",
        "",
        "Every seed above passes and is in `REGRESSION_SEEDS`.",
    ]
    return "\n".join(lines) + "\n"


def _self_test() -> int:
    report = f"""<testsuites><testsuite>
<testcase classname="t" name="{_TEST}[walk-20261007]"/>
<testcase name="{_TEST}[hater-20261008]"><failure message="m">E  bar 12: rule 5.4 broken
AssertionError</failure></testcase>
<testcase classname="t" name="{_TEST}[walk-20261009]"><error message="c">boom</error></testcase>
<testcase classname="t" name="test_seeds_come_from_the_env"><failure>x</failure></testcase>
</testsuite></testsuites>"""
    failed = 0
    found = failing_seeds(report)
    expected = [
        ("hater", 20261008, "E  bar 12: rule 5.4 broken\nAssertionError"),
        ("walk", 20261009, "boom"),
    ]
    if found == expected:
        print("PASS: failed and errored seeds are found; passing seeds and other tests are not")
    else:
        print(f"FAIL: expected {expected}, got {found}")
        failed = 1
    body = issue_body(found, "300@20367000")
    for needle in (
        "HERMES_SCENARIO_SEEDS=300@20367000",
        "### `hater-20261008`",
        "rule 5.4 broken",
        f"HERMES_SCENARIO_SEEDS=1@20261009 uv run pytest "
        f"'tests/integration/scenarios/test_generated_paths.py::{_TEST}[walk-20261009]'",
        "REGRESSION_SEEDS",
    ):
        if needle in body:
            print(f"PASS: body has {needle[:50]!r}")
        else:
            print(f"FAIL: body lacks {needle!r}")
            failed = 1
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean.xml"
        clean.write_text(f'<testsuite><testcase name="{_TEST}[walk-1]"/></testsuite>')
        if _main([str(clean), "1@1"]) == 0:
            print("PASS: a clean run exits 0")
        else:
            print("FAIL: a clean run did not exit 0")
            failed = 1
    print("\nSELF-TEST FAILED" if failed else "\nAll self-tests passed.")
    return failed


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: fresh-seed-report.py <junit.xml> <seeds-spec>", file=sys.stderr)
        return 2
    try:
        failures = failing_seeds(Path(argv[0]).read_text(encoding="utf-8"))
    except (OSError, ET.ParseError) as exc:
        print(f"ERROR: cannot read the report {argv[0]}: {exc}", file=sys.stderr)
        return 2
    if not failures:
        return 0
    sys.stdout.write(issue_body(failures, argv[1]))
    return 1


def main() -> int:
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return _self_test()
    return _main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
