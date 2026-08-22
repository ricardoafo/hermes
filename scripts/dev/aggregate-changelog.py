#!/usr/bin/env python3
"""Aggregate changelog.d/*.md fragments into a single [Unreleased] block.

Introduced by process hardening wave 4. See ``changelog.d/README.md`` for
the naming convention and rationale.

Behavior
--------
- Reads every file under ``changelog.d/`` whose name matches
  ``<digits>-<type>.md`` where type ∈ SECTIONS.
- Skips ``.gitkeep``, ``README.md``, and any file whose name does NOT
  start with a digit (placeholder convention — human-only fragments).
- Groups fragments by section and orders each group by numeric PR
  prefix (ascending).
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
- 2 — invalid fragment filename (unknown type suffix) or CHANGELOG.md
  missing when --apply is set.
"""

from __future__ import annotations

import argparse
import re
import sys
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
UNRELEASED_RE = re.compile(
    r"^## \[Unreleased\].*?(?=^## \[|\Z)",
    re.DOTALL | re.MULTILINE,
)


def _repo_root() -> Path:
    """Return the repo root (this script lives in ``scripts/dev/``)."""
    return Path(__file__).resolve().parent.parent.parent


def collect_fragments(dir_path: Path) -> dict[str, list[tuple[int, str]]]:
    """Group fragments by section.

    Returns a mapping ``section-title -> [(pr_number, body), ...]`` sorted
    by PR number ascending within each section. Non-numeric-prefixed files
    (placeholder convention) are skipped.

    Raises
    ------
    ValueError
        If a numeric-prefixed file has an unrecognised type suffix.
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
        m = FRAGMENT_RE.match(path.name)
        if m is None:
            # Placeholder / non-numeric — silently skip per convention.
            continue
        type_key = m.group("type")
        if type_key not in valid_types:
            raise ValueError(
                f"changelog.d/{path.name}: unknown type '{type_key}' — "
                f"expected one of: {', '.join(valid_types)}"
            )
        pr = int(m.group("pr"))
        body = path.read_text(encoding="utf-8").rstrip() + "\n"
        section = valid_types[type_key]
        by_section[section].append((pr, body))

    for entries in by_section.values():
        entries.sort(key=lambda x: x[0])
    return by_section


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
