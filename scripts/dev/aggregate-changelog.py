#!/usr/bin/env python3
"""Aggregate changelog.d/*.md fragments into a single [Unreleased] block.

Introduced by process hardening wave 4. See ``changelog.d/README.md`` for
the naming convention and rationale.

Behavior
--------
- Reads every file under ``changelog.d/`` whose name matches
  ``<number>-<type>.md`` (a PR or issue number) or ``+<slug>.<type>.md``
  (an orphan fragment, named before the PR exists; #420), where type ∈
  SECTIONS.
- An orphan's number is resolved at release time from the squash commit
  that added it (subject ending in ``(#<pr>)``). Unresolved orphans sort
  last, by slug.
- Skips ``.gitkeep`` and ``README.md``. Any other name is an error: a
  file the aggregator skipped would be missing from every release's notes
  (#1172).
- Groups fragments by section and orders each group by number
  (ascending).
- Emits a merged ``[Unreleased]`` block in Keep-a-Changelog order.

Modes
-----
- Default: prints the merged block to stdout (dry run).
- ``--apply``: replaces the existing ``[Unreleased]`` block in
  ``CHANGELOG.md`` in place. Preserves everything outside that block
  byte-for-byte (header, link references, prior release sections).

Exit codes
----------
- 0 — success (dry run or --apply completed).
- 1 — no fragments found AND no existing [Unreleased] to preserve
  (nothing to do; probably a mistake).
- 2 — invalid fragment filename (unknown name or type suffix), a fragment
  that is not one top-level bullet, or CHANGELOG.md missing when --apply
  is set.
"""

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# Keep-a-Changelog section order (case-sensitive header text)
SECTIONS: tuple[str, ...] = (
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
)

FRAGMENT_RE = re.compile(r"^(?P<pr>\d+)-(?P<type>[a-z]+)\.md$")
ORPHAN_RE = re.compile(r"^\+(?P<slug>[a-z0-9][a-z0-9._-]*)\.(?P<type>[a-z]+)\.md$")
SQUASH_PR_RE = re.compile(r"\(#(?P<pr>\d+)\)\s*$")
UNRESOLVED = 10**9
"""Sort key for an orphan whose PR could not be resolved: after every number."""
UNRELEASED_RE = re.compile(
    r"^## \[Unreleased\].*?(?=^## \[|\Z)",
    re.DOTALL | re.MULTILINE,
)


def _repo_root() -> Path:
    """Return the repo root (this script lives in ``scripts/dev/``)."""
    return Path(__file__).resolve().parent.parent.parent


def git_added_pr(path: Path) -> int | None:
    """Return the PR number of the squash commit that added ``path``, if any.

    Squash merges end their subject with ``(#<pr>)``. Returns ``None`` when
    git is unavailable or the file was not added by such a commit.
    """
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv; git resolved via shutil.which
            [git, "log", "--diff-filter=A", "--format=%s", "--", path.name],
            cwd=path.parent,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return pr_from_subjects(result.stdout.splitlines())


def pr_from_subjects(subjects: list[str]) -> int | None:
    """Return the PR number from the first squash subject ending in ``(#<pr>)``."""
    for subject in subjects:
        m = SQUASH_PR_RE.search(subject)
        if m is not None:
            return int(m.group("pr"))
    return None


def collect_fragments(
    dir_path: Path, resolve_pr: Callable[[Path], int | None] = git_added_pr
) -> dict[str, list[tuple[int, str]]]:
    """Group fragments by section.

    Returns a mapping ``section-title -> [(number, body), ...]`` sorted by
    number ascending within each section. Orphan fragments take the number
    ``resolve_pr`` returns, or :data:`UNRESOLVED`.

    Raises
    ------
    ValueError
        If a file matches neither fragment name, has an unrecognised type
        suffix, or fails :func:`check_shape`.
    """
    by_section: dict[str, list[tuple[int, str]]] = {s: [] for s in SECTIONS}
    if not dir_path.is_dir():
        return by_section

    valid_types = {s.lower(): s for s in SECTIONS}

    for path in sorted(dir_path.iterdir()):
        if not path.is_file():
            continue
        if path.name in {".gitkeep", "README.md"}:
            continue
        numbered = FRAGMENT_RE.match(path.name)
        match = numbered or ORPHAN_RE.match(path.name)
        if match is None:
            raise ValueError(
                f"changelog.d/{path.name}: not a fragment name — "
                "expected <number>-<type>.md or +<slug>.<type>.md"
            )
        type_key = match.group("type")
        if type_key not in valid_types:
            raise ValueError(
                f"changelog.d/{path.name}: unknown type '{type_key}' — "
                f"expected one of: {', '.join(valid_types)}"
            )
        pr = int(numbered.group("pr")) if numbered else (resolve_pr(path) or UNRESOLVED)
        body = path.read_text(encoding="utf-8").rstrip() + "\n"
        check_shape(path.name, body)
        section = valid_types[type_key]
        by_section[section].append((pr, body))

    for entries in by_section.values():
        entries.sort(key=lambda x: x[0])
    return by_section


def check_shape(name: str, body: str) -> None:
    """Refuse a fragment that is not exactly one top-level bullet.

    A header inside a fragment would open a stray section in the release
    notes, and a second top-level bullet hides one note inside another's
    file (#1172).

    Raises
    ------
    ValueError
        If ``body`` does not start with ``- ``, holds a second top-level
        bullet, or holds a Markdown header.
    """
    lines = body.splitlines()
    bullets = sum(line.startswith("- ") for line in lines)
    if not body.startswith("- ") or bullets != 1:
        raise ValueError(
            f"changelog.d/{name}: expected exactly one top-level '- ' bullet, found {bullets}"
        )
    if any(line.startswith("#") for line in lines):
        raise ValueError(f"changelog.d/{name}: no headers; the type comes from the filename")


def render_unreleased(by_section: dict[str, list[tuple[int, str]]]) -> str:
    """Render the merged ``[Unreleased]`` block.

    Emits only sections with at least one fragment — a section with zero
    entries is omitted entirely (matches the current CHANGELOG.md style).
    """
    lines: list[str] = ["## [Unreleased]", ""]
    for section in SECTIONS:
        entries = by_section.get(section, [])
        if not entries:
            continue
        lines.append(f"### {section}")
        lines.append("")
        for _pr, body in entries:
            lines.append(body.rstrip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def apply_to_changelog(changelog_path: Path, new_block: str) -> None:
    """Replace the ``[Unreleased]`` block in-place, preserving the rest."""
    if not changelog_path.is_file():
        raise FileNotFoundError(f"CHANGELOG.md not found: {changelog_path}")
    original = changelog_path.read_text(encoding="utf-8")
    if not UNRELEASED_RE.search(original):
        raise ValueError(f"{changelog_path}: no '## [Unreleased]' section found to replace")
    # Preserve trailing blank line between old block and next section.
    replaced = UNRELEASED_RE.sub(new_block.rstrip() + "\n\n", original, count=1)
    changelog_path.write_text(replaced, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point — parse args and dispatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite CHANGELOG.md [Unreleased] block in place.",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Path to changelog.d/ (default: <repo-root>/changelog.d/).",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=None,
        help="Path to CHANGELOG.md (default: <repo-root>/CHANGELOG.md).",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    frag_dir = args.dir or (root / "changelog.d")
    changelog = args.changelog or (root / "CHANGELOG.md")

    try:
        by_section = collect_fragments(frag_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    block = render_unreleased(by_section)
    total = sum(len(v) for v in by_section.values())

    if args.apply:
        try:
            apply_to_changelog(changelog, block)
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(
            f"Applied {total} fragment(s) to {changelog.relative_to(root)}.",
            file=sys.stderr,
        )
        return 0

    if total == 0:
        # Dry-run with nothing to do — still emit the empty header for
        # downstream diffing / debugging.
        print(block, end="")
        return 0

    print(block, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
