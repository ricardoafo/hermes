#!/usr/bin/env python3
"""Keep ``openspec/changes/`` to live work and every spec path resolvable (#1196).

A change spec is live while its tracking issue is open. Once the issue
closes, the spec moves to ``openspec/archive/<name>/`` with its status set to
``Shipped`` (closed as completed) or ``Withdrawn`` (closed as not planned).
The archive is kept, never deleted, but a root ``.ignore`` keeps it out of
repo-wide searches, so agents looking something up read the code and the
live specs first. Name the folder to search it:
``rg <pattern> openspec/archive``.

``--check`` (the default, offline, run by the ``openspec-layout`` pre-commit
hook and CI's ``guards`` check) fails when:

- a folder under ``openspec/changes/`` has no ``issue.md`` with an
  ``issue: <N>`` line;
- an archived ``change.md`` has a ``**Status**`` other than Shipped or
  Withdrawn;
- a tracked file names ``openspec/changes/<name>`` or
  ``openspec/archive/<name>`` for a folder that is not there, which is how a
  move would leave stale pointers behind. Release notes (``CHANGELOG.md``,
  ``changelog.d/``) keep the paths they shipped with and are not checked.

``--closed`` asks GitHub for each live change's issue and fails when one is
closed. ``--sweep`` archives those changes: ``git mv``, the status line, and
every reference in tracked files. Both ask GitHub, so they run at the
release cut rather than in CI.

Usage:
    python scripts/dev/openspec-archive.py [--check | --closed | --sweep]

Exit codes:
    0  nothing to report (or the sweep finished)
    1  a problem was found (printed one per line)
    2  usage error, or GitHub could not be asked
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

CHANGES = Path("openspec/changes")
ARCHIVE = Path("openspec/archive")
TEMPLATE = "TEMPLATE"
FINAL_STATUSES = ("Shipped", "Withdrawn")
# Placeholder names: the dev scripts' self-tests create ``x-change`` in a scratch
# repo, and specs use ``foo``/``foo-bar`` in worked examples.
EXAMPLE_NAMES = frozenset({"x-change", "foo", "foo-bar"})

# Release notes record the paths as they were when each version shipped, and
# this check's own tests name made-up specs in their fixtures.
NOT_SCANNED = (
    "CHANGELOG.md",
    "changelog.d/",
    "tests/unit/scripts_dev/test_openspec_archive.py",
)

_ISSUE_LINE = re.compile(r"^issue:\s*(\d+)\s*$", re.MULTILINE)
_STATUS_LINE = re.compile(r"^\*\*Status\*\*:[^\n]*$", re.MULTILINE)
_REFERENCE = re.compile(r"openspec/(changes|archive)/([A-Za-z0-9][A-Za-z0-9_-]*)")


def _run(tool: str, *args: str) -> str:
    """Run ``tool`` with ``args`` and return its standard output."""
    executable = shutil.which(tool)
    if executable is None:
        raise FileNotFoundError(f"{tool} is not on PATH")
    return subprocess.run(  # noqa: S603 — fixed argv; the tool is resolved via shutil.which
        [executable, *args], capture_output=True, text=True, check=True
    ).stdout


def issue_number(issue_md: str) -> int | None:
    """Return the ``issue: <N>`` number in an ``issue.md``, or ``None``."""
    match = _ISSUE_LINE.search(issue_md)
    return int(match.group(1)) if match else None


def status_of(change_md: str) -> str | None:
    """Return the word after ``**Status**:`` in a ``change.md``, or ``None``."""
    match = _STATUS_LINE.search(change_md)
    if not match:
        return None
    words = match.group(0).split(":", 1)[1].split()
    return words[0] if words else ""


def with_status(change_md: str, status: str) -> str:
    """Return ``change_md`` with its status line set to ``status``."""
    line = f"**Status**: {status}"
    if _STATUS_LINE.search(change_md):
        return _STATUS_LINE.sub(line, change_md, count=1)
    title, _, rest = change_md.partition("\n")
    return f"{title}\n\n{line}\n{rest}"


def _folders(parent: Path) -> list[Path]:
    return sorted(p for p in parent.iterdir() if p.is_dir()) if parent.is_dir() else []


def live_changes(root: Path) -> list[Path]:
    """Return every change folder under ``openspec/changes/`` except the template."""
    return [p for p in _folders(root / CHANGES) if p.name != TEMPLATE]


def tracked_text_files(root: Path) -> list[Path]:
    """Return every file git tracks under ``root`` except ``NOT_SCANNED``."""
    listed = _run("git", "-C", str(root), "ls-files", "-z").split("\0")
    return [root / name for name in listed if name and not name.startswith(NOT_SCANNED)]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError, FileNotFoundError, IsADirectoryError:
        return None


def dangling_references(root: Path, files: Iterable[Path]) -> list[str]:
    """Return one message per spec path that names a missing folder."""
    problems: list[str] = []
    for path in files:
        text = _read_text(path)
        if text is None or "openspec/" not in text:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for kind, raw_name in _REFERENCE.findall(line):
                name = raw_name.rstrip("-_")
                if name in EXAMPLE_NAMES or (root / "openspec" / kind / name).is_dir():
                    continue
                problems.append(
                    f"{path.relative_to(root)}:{number}: openspec/{kind}/{name} does not exist"
                )
    return problems


def layout_problems(root: Path, files: Iterable[Path]) -> list[str]:
    """Return every ``--check`` violation under ``root``."""
    problems: list[str] = []
    for change in live_changes(root):
        text = _read_text(change / "issue.md")
        if text is None or issue_number(text) is None:
            problems.append(f"{change.relative_to(root)}: issue.md needs an 'issue: <N>' line")
    for change in _folders(root / ARCHIVE):
        text = _read_text(change / "change.md")
        status = None if text is None else status_of(text)
        if status is not None and status not in FINAL_STATUSES:
            problems.append(
                f"{change.relative_to(root)}/change.md: an archived change is Shipped or "
                f"Withdrawn, not {status!r}"
            )
    problems.extend(dangling_references(root, files))
    return problems


def _issue_state(number: int) -> tuple[str, str]:
    issue = json.loads(_run("gh", "api", f"repos/{{owner}}/{{repo}}/issues/{number}"))
    return issue["state"], issue.get("state_reason") or ""


def closed_changes(root: Path) -> dict[Path, str]:
    """Map each live change whose issue is closed to its final status."""
    closed: dict[Path, str] = {}
    for change in live_changes(root):
        text = _read_text(change / "issue.md")
        number = None if text is None else issue_number(text)
        if number is None:
            continue
        state, reason = _issue_state(number)
        if state == "closed":
            closed[change] = "Withdrawn" if reason == "not_planned" else "Shipped"
    return closed


def rewrite_references(text: str, names: Iterable[str]) -> str:
    """Point every ``openspec/changes/<name>`` for ``names`` at the archive."""
    for name in names:
        text = re.sub(
            rf"openspec/changes/{re.escape(name)}(?![\w-])", f"openspec/archive/{name}", text
        )
    return text


def sweep(root: Path, closed: dict[Path, str]) -> None:
    """Archive every change in ``closed`` and repoint the references to it."""
    if not closed:
        return
    (root / ARCHIVE).mkdir(parents=True, exist_ok=True)
    for change, status in closed.items():
        target = root / ARCHIVE / change.name
        _run("git", "-C", str(root), "mv", str(change), str(target))
        change_md = target / "change.md"
        text = _read_text(change_md)
        if text is not None:
            change_md.write_text(with_status(text, status), encoding="utf-8")
    names = sorted((change.name for change in closed), key=len, reverse=True)
    for path in tracked_text_files(root):
        text = _read_text(path)
        if text is None or "openspec/changes/" not in text:
            continue
        rewritten = rewrite_references(text, names)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the check, the closed-issue report, or the sweep."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="offline layout check (default)")
    mode.add_argument("--closed", action="store_true", help="list changes whose issue closed")
    mode.add_argument("--sweep", action="store_true", help="archive changes whose issue closed")
    args = parser.parse_args(argv)
    root = Path(_run("git", "rev-parse", "--show-toplevel").strip())

    if args.closed or args.sweep:
        try:
            closed = closed_changes(root)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as error:
            print(f"ERROR: could not read the tracking issues: {error}", file=sys.stderr)
            return 2
        if args.sweep:
            sweep(root, closed)
            for change, status in closed.items():
                print(f"archived {change.relative_to(root)} as {status}")
            return 0
        for change, status in closed.items():
            print(f"{change.relative_to(root)}: issue closed; archive it as {status} (--sweep)")
        return 1 if closed else 0

    problems = layout_problems(root, tracked_text_files(root))
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
