#!/usr/bin/env python3
"""Warn a writer who changes an existing test assertion (#847).

A test that fails after a behaviour change is usually the test doing its job.
Editing its assertion to make it pass hides the defect it caught (AGENTS.md
§ Writing tests: "When updating a test to accommodate a code change: STOP").
This hook makes that edit visible to the writer at the moment it happens and
asks for the reason.

Called by the ``.claude/settings.json`` PostToolUse hook for Edit and
MultiEdit. It reads the hook's JSON on stdin. An assertion counts as existing
when its line is in the file as committed at HEAD, so a writer reshaping a
test it has just written is not warned.

Invocation:

    uv run --no-sync python scripts/dev/assertion-edit-warning.py < hook-input.json
    HERMES_SELF_TEST=1 uv run --no-sync python scripts/dev/assertion-edit-warning.py

Exit codes:

- 0: no existing assertion changed, or the input is not something to check
- 2: an existing assertion changed; stderr goes back to the writer
"""

import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

_TEST_FILE = re.compile(r"(^|/)tests/.*\.py$")
_ASSERTION = re.compile(r"^(assert\b|self\.assert\w*\(|with pytest\.raises\(|pytest\.raises\()")


def _assertion_lines(text: str) -> Counter[str]:
    return Counter(line.strip() for line in text.splitlines() if _ASSERTION.match(line.strip()))


def _committed(path: str) -> str:
    git = shutil.which("git")
    if git is None:
        return ""
    try:
        repo = subprocess.run(  # noqa: S603 — fixed argv; git resolved via shutil.which
            [git, "-C", str(Path(path).parent), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        relative = Path(path).resolve().relative_to(Path(repo).resolve()).as_posix()
        return subprocess.run(  # noqa: S603 — fixed argv; git resolved via shutil.which
            [git, "-C", repo, "show", f"HEAD:{relative}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except OSError, ValueError, subprocess.CalledProcessError:
        return ""


def changed_assertions(hook_input: dict[str, object], committed: Callable[[str], str]) -> list[str]:
    """Return the committed assertion lines this edit removed or rewrote."""
    tool_input = hook_input.get("tool_input")
    if hook_input.get("tool_name") not in {"Edit", "MultiEdit"} or not isinstance(tool_input, dict):
        return []
    path = tool_input.get("file_path")
    if not isinstance(path, str) or not _TEST_FILE.search(path.replace("\\", "/")):
        return []
    edits = tool_input.get("edits") if hook_input["tool_name"] == "MultiEdit" else [tool_input]
    if not isinstance(edits, list):
        return []
    removed: Counter[str] = Counter()
    for edit in edits:
        if isinstance(edit, dict):
            removed += _assertion_lines(str(edit.get("old_string", ""))) - _assertion_lines(
                str(edit.get("new_string", ""))
            )
    if not removed:
        return []
    existing = _assertion_lines(committed(path))
    return sorted(line for line in removed if line in existing)


def _message(path: str, lines: list[str]) -> str:
    shown = "\n".join(f"  {line}" for line in lines)
    return (
        f"You changed an existing assertion in {path}:\n{shown}\n"
        "If this test failed because of your change, it may be catching the defect. "
        "Silencing it hides the bug (AGENTS.md, Writing tests): stop and check first.\n"
        "If the old assertion was wrong, say why in the commit message and list it in "
        "the PR body. On a fix: task, the test that proves the fix is a new or "
        "stricter one, not a looser old one."
    )


def _self_test() -> int:
    committed_file = "def test_x():\n    assert total == Decimal('12.50')\n"

    def committed(_: str) -> str:
        return committed_file

    def edit(path: str, old: str, new: str, tool: str = "Edit") -> dict[str, object]:
        return {
            "tool_name": tool,
            "tool_input": {"file_path": path, "old_string": old, "new_string": new},
        }

    old = "    assert total == Decimal('12.50')\n"
    cases: list[tuple[str, dict[str, object], list[str]]] = [
        (
            "loosened committed assertion is caught",
            edit("/r/tests/unit/test_a.py", old, "    assert total\n"),
            ["assert total == Decimal('12.50')"],
        ),
        (
            "deleted committed assertion is caught",
            edit("/r/tests/unit/test_a.py", old, ""),
            ["assert total == Decimal('12.50')"],
        ),
        (
            "multi-edit is checked",
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "/r/tests/unit/test_a.py",
                    "edits": [{"old_string": old, "new_string": ""}],
                },
            },
            ["assert total == Decimal('12.50')"],
        ),
        (
            "an added assertion is not caught",
            edit("/r/tests/unit/test_a.py", old, old + "    assert fee == 0\n"),
            [],
        ),
        (
            "an assertion not yet committed is not caught",
            edit("/r/tests/unit/test_a.py", "    assert fee == 1\n", "    assert fee == 2\n"),
            [],
        ),
        ("source files are not checked", edit("/r/src/hermes/a.py", old, ""), []),
        (
            "Windows paths are checked",
            edit("C:\\r\\tests\\unit\\test_a.py", old, ""),
            ["assert total == Decimal('12.50')"],
        ),
        ("Write is not checked", edit("/r/tests/unit/test_a.py", old, "", tool="Write"), []),
    ]
    failed = 0
    for name, hook_input, expected in cases:
        got = changed_assertions(hook_input, committed)
        if got == expected:
            print(f"PASS: {name}")
        else:
            print(f"FAIL: {name}: expected {expected}, got {got}")
            failed = 1
    print("\nSELF-TEST FAILED" if failed else "\nAll self-tests passed.")
    return failed


def main() -> int:
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return _self_test()
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError, UnicodeDecodeError:
        return 0
    if not isinstance(hook_input, dict):
        return 0
    lines = changed_assertions(hook_input, _committed)
    if not lines:
        return 0
    print(_message(str(hook_input["tool_input"]["file_path"]), lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
