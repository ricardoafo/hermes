#!/usr/bin/env python3
"""ADR hygiene guard (issue #1174).

Two rules over every ADR file under ``docs/architecture``:

1. A merged ADR is never ``Proposed`` or ``Draft``. ADR-009 makes ``Accepted`` the
   trigger for immutability, so a status left at ``Proposed`` after merge leaves the
   ADR unprotected. ADRs that are still ``Proposed`` today are listed in
   ``scripts/dev/adr-status.baseline`` (shrink only: delete a line once the ADR is
   flipped; a line for a file that is Accepted fails the run).
2. Every ADR a ``**Supersedes**`` field names must say so itself: the named file
   mentions the superseding ADR's number in its header (the first lines).

Usage:
    python3 scripts/dev/adr-status-check.py
    HERMES_SELF_TEST=1 python3 scripts/dev/adr-status-check.py

Exit codes:
    0  every rule holds (or the self-test passed)
    1  a rule is broken
"""

import os
import re
import sys
import tempfile
from pathlib import Path

ADR_DIR = Path("docs") / "architecture"
BASELINE = Path("scripts") / "dev" / "adr-status.baseline"
_HEADER_LINES = 15
_UNMERGED = re.compile(r"^(proposed|draft)\b", re.IGNORECASE)
_ADR_NUMBER = re.compile(r"ADR-(\d{3})")
_FILE_NAME = re.compile(r"adr-(\d{3})-")
_LINK = re.compile(r"\]\(([^)#]+\.md)")
_NO_SUPERSESSION = re.compile(r"^(none|nothing|no one|—|-)", re.IGNORECASE)
_SENTENCE_END = re.compile(r"\.\s+(?=[A-Z])")


def adr_files(root: Path) -> list[Path]:
    """Return every ADR markdown file: ``adr-NNN-*.md`` and the sections of folder ADRs."""
    base = root / ADR_DIR
    return sorted(
        path for path in base.rglob("*.md") if _FILE_NAME.search(path.relative_to(base).parts[0])
    )


def header_field(text: str, name: str) -> str | None:
    """Return the value of ``**name**:`` in the header, joining its wrapped lines."""
    lines = text.splitlines()[:_HEADER_LINES]
    for index, line in enumerate(lines):
        if line.startswith(f"**{name}**:"):
            value = [line.split(":", 1)[1].strip()]
            for follow in lines[index + 1 :]:
                if not follow.strip() or follow.startswith(("**", "#", "---")):
                    break
                value.append(follow.strip())
            return " ".join(value)
    return None


def own_number(path: Path, root: Path) -> str | None:
    """Return the ADR number a file belongs to, from its top folder or file name."""
    match = _FILE_NAME.search(path.relative_to(root / ADR_DIR).parts[0])
    return match.group(1) if match else None


def read_baseline(root: Path) -> set[str]:
    """Return the repo-relative paths allowed to stay Proposed."""
    path = root / BASELINE
    if not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def check_statuses(root: Path) -> list[str]:
    """Report ADRs that read Proposed or Draft and are not in the baseline."""
    allowed = read_baseline(root)
    problems: list[str] = []
    seen: set[str] = set()
    for path in adr_files(root):
        rel = path.relative_to(root).as_posix()
        status = header_field(path.read_text(encoding="utf-8"), "Status")
        if status is None:
            continue
        unmerged = bool(_UNMERGED.match(status))
        if unmerged and rel not in allowed:
            problems.append(f"{rel}: status is '{status[:40]}'; a merged ADR is Accepted")
        if not unmerged and rel in allowed:
            problems.append(f"{rel}: Accepted now; delete it from {BASELINE.as_posix()}")
        seen.add(rel)
    return problems


def superseded_targets(path: Path, root: Path, field: str) -> list[Path]:
    """Resolve the ADR files a Supersedes field points at."""
    targets: list[Path] = []
    if _NO_SUPERSESSION.match(field):
        return targets
    # Only the first sentence names what is superseded; later ones say what
    # "stands" and may link ADRs that are not touched.
    first_sentence = _SENTENCE_END.split(field, maxsplit=1)[0]
    for link in _LINK.findall(first_sentence):
        resolved = (path.parent / link).resolve()
        if resolved.is_file() and (root / ADR_DIR).resolve() in resolved.parents:
            targets.append(resolved)
    return targets


def check_back_links(root: Path) -> list[str]:
    """Report superseded ADRs that never mention the ADR that supersedes them."""
    problems: list[str] = []
    for path in adr_files(root):
        field = header_field(path.read_text(encoding="utf-8"), "Supersedes")
        mine = own_number(path, root)
        if not field or mine is None:
            continue
        for target in superseded_targets(path, root, field):
            if own_number(target, root) == mine:
                continue
            header = "\n".join(target.read_text(encoding="utf-8").splitlines()[:_HEADER_LINES])
            if f"ADR-{mine}" not in header:
                problems.append(
                    f"{target.relative_to(root).as_posix()}: supersedes link missing; "
                    f"{path.name} supersedes it, so its header must name ADR-{mine}"
                )
    return problems


def run(root: Path) -> list[str]:
    """Return every problem found under ``root``."""
    return check_statuses(root) + check_back_links(root)


def _write(root: Path, name: str, body: str) -> None:
    path = root / ADR_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def self_test() -> int:
    """Run built-in pass and fail cases; return the exit code."""
    failures: list[str] = []

    def case(label: str, setup: dict[str, str], baseline: str, expect: int) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in setup.items():
                _write(root, name, body)
            (root / BASELINE).parent.mkdir(parents=True)
            (root / BASELINE).write_text(baseline, encoding="utf-8")
            got = len(run(root))
            if got != expect:
                failures.append(f"{label}: expected {expect} problems, got {got}")

    accepted = "# ADR-001: a\n\n**Status**: Accepted (merged in PR #1)\n**Supersedes**: none\n"
    case("accepted passes", {"adr-001-a.md": accepted}, "", 0)
    case(
        "proposed fails",
        {"adr-001-a.md": "# ADR-001: a\n\n**Status**: Proposed. Accepted on merge\n"},
        "",
        1,
    )
    case(
        "proposed in the baseline passes",
        {"adr-001-a.md": "# ADR-001: a\n\n**Status**: Proposed\n"},
        "docs/architecture/adr-001-a.md\n",
        0,
    )
    case(
        "accepted in the baseline fails",
        {"adr-001-a.md": accepted},
        "docs/architecture/adr-001-a.md\n",
        1,
    )
    newer = "# ADR-002: b\n\n**Status**: Accepted\n**Supersedes**: [ADR-001](adr-001-a.md) §1\n"
    case("missing back link fails", {"adr-001-a.md": accepted, "adr-002-b.md": newer}, "", 1)
    linked = (
        "# ADR-001: a\n\n**Status**: Accepted\n**Superseded in part by**: [ADR-002](adr-002-b.md)\n"
    )
    case("back link passes", {"adr-001-a.md": linked, "adr-002-b.md": newer}, "", 0)
    wrapped = (
        "# ADR-002: b\n\n**Status**: Accepted\n"
        "**Supersedes**: in part,\n[ADR-001 §1](adr-001-a/01-x.md)\n"
    )
    section = "# S\n\n**Status**: Accepted\n"
    case(
        "section target without a back link fails",
        {"adr-001-a/01-x.md": section, "adr-002-b.md": wrapped},
        "",
        1,
    )
    if failures:
        print("SELF-TEST FAILED:\n  " + "\n  ".join(failures))
        return 1
    print("SELF-TEST PASS: adr-status-check.py")
    return 0


def main() -> int:
    """Run the guard from the repo root."""
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return self_test()
    problems = run(Path.cwd())
    if problems:
        print("adr-status-check:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
