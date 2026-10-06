#!/usr/bin/env python3
"""Rewrite a Dependabot PR body into the 4-section PR template.

Dependabot bodies carry release notes and commit lists but none of the
mandatory sections ``scripts/dev/pr-body-lint.sh`` checks. This script
renders Summary / Related / Testing / Risk from the PR title and the
original body, and keeps the original body verbatim in a trailing
``## Dependabot notes`` section so nothing the reviewer needs is lost.

- **Summary**: one line per update (package, old and new version).
- **Related**: ADR-010, the dependency-upgrade checklist, and the
  upstream links Dependabot already lists (repo, releases, CHANGELOG).
- **Testing**: what CI covers and the checklist the reviewer still owns.
- **Risk**: a loud MAJOR-bump warning when any update crosses a major
  boundary (or a minor boundary below 1.0), otherwise low.

Run by ``.github/workflows/pr-housekeeping.yml``. Reads ``PR_TITLE``
and ``PR_BODY`` from the environment and writes the rendered body to
``--out``.

Exit codes
----------
- 0 — body rendered to ``--out``.
- 2 — usage error (missing ``PR_TITLE``).
- 3 — body already rewritten (marker present); nothing written.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MARKER = "<!-- hermes:dependabot-body-rewritten -->"
EXIT_ALREADY_REWRITTEN = 3
_MAX_LINKS = 10

_TITLE_UPDATE = re.compile(
    r"(?:bump|update)\s+(?P<pkg>\S+?)(?:\s+requirement)?\s+from\s+(?P<old>\S+)\s+to\s+(?P<new>\S+)",
    re.IGNORECASE,
)
_BODY_UPDATE = re.compile(
    r"Updates? `(?P<pkg>[^`]+)` from (?P<old>\S+) to (?P<new>\S+?)\.?$", re.MULTILINE
)
_FLOOR = re.compile(r">=\s*(\d+(?:\.\d+)*)")
_VERSION = re.compile(r"\d+(?:\.\d+)*")
_MD_LINK = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<url>https://github\.com/[^)\s]+)\)")
_HREF = re.compile(
    r'href="(?P<url>https://github\.com/[^"]+/(?:releases|blob/[^"]+/CHANGELOG[^"]*))"'
)


@dataclass(frozen=True, slots=True)
class Update:
    """One package version change parsed from the title or body."""

    package: str
    old: str
    new: str

    @property
    def is_major(self) -> bool:
        """Return True when the update crosses a semver-breaking boundary."""
        old, new = _parts(self.old), _parts(self.new)
        if not old or not new:
            return False
        if new[0] != old[0]:
            return new[0] > old[0]
        if new[0] == 0 and len(old) > 1 and len(new) > 1:
            return new[1] > old[1]
        return False


def _floor_version(spec: str) -> str:
    """Return the version a spec resolves to: its ``>=`` floor, else its first version."""
    floor = _FLOOR.search(spec)
    if floor:
        return floor.group(1)
    first = _VERSION.search(spec)
    return first.group(0) if first else spec


def _parts(version: str) -> tuple[int, ...]:
    """Return the integer components of ``version`` (``"22.8"`` → ``(22, 8)``)."""
    found = _VERSION.search(version)
    if not found:
        return ()
    return tuple(int(p) for p in found.group(0).split("."))


def parse_updates(title: str, body: str) -> list[Update]:
    """Return every update named in the body, falling back to the title.

    Grouped Dependabot PRs list each package as ``Updates `pkg` from A to B``
    in the body; single-package PRs carry it only in the title.
    """
    updates = [
        Update(m["pkg"], _floor_version(m["old"]), _floor_version(m["new"]))
        for m in _BODY_UPDATE.finditer(body)
    ]
    if updates:
        return updates
    title_match = _TITLE_UPDATE.search(title)
    if title_match is None:
        return []
    return [
        Update(
            title_match["pkg"],
            _floor_version(title_match["old"]),
            _floor_version(title_match["new"]),
        )
    ]


def upstream_links(body: str) -> list[str]:
    """Return de-duplicated upstream links (repo, releases, CHANGELOG) as Markdown bullets."""
    seen: set[str] = set()
    links: list[str] = []
    for m in _MD_LINK.finditer(body):
        if m["url"] not in seen:
            seen.add(m["url"])
            links.append(f"- Upstream: [{m['text']}]({m['url']})")
    for m in _HREF.finditer(body):
        if m["url"] not in seen:
            seen.add(m["url"])
            links.append(f"- Release notes: <{m['url']}>")
    return links[:_MAX_LINKS]


def render(title: str, body: str) -> str:
    """Render the 4-section body for a Dependabot PR."""
    updates = parse_updates(title, body)
    if updates:
        summary = "\n".join(f"- `{u.package}`: {u.old} → {u.new}" for u in updates)
    else:
        summary = f"- {title}"
    majors = [u for u in updates if u.is_major]
    if majors:
        names = ", ".join(f"`{u.package}` {u.old} → {u.new}" for u in majors)
        risk = (
            f"> [!WARNING]\n> **MAJOR bump**: {names}. Read the upstream CHANGELOG for "
            f"breaking changes and run the full checklist before approving."
        )
    elif updates:
        risk = "Low: no update crosses a major version boundary."
    else:
        risk = "Unknown: the versions could not be parsed from the title or body. Review manually."
    related = [
        "- ADR: ADR-010 (dependency management and supply chain)",
        "- Issue: N/A (Dependabot update)",
        "- Checklist: `docs/decisions/2026-08-24-dependency-upgrade-checklist.md`",
        *upstream_links(body),
    ]
    sections = [
        MARKER,
        "## Summary",
        f"Dependabot update, reformatted to the PR template.\n\n{summary}",
        "## Related",
        "\n".join(related),
        "## Testing",
        "- CI on this PR: unit tests, `pip-audit`, license check and lockfile integrity.\n"
        "- Reviewer: follow the dependency-upgrade checklist; the original release "
        "notes are under Dependabot notes below.",
        "## Risk",
        risk,
        "## Dependabot notes",
        body.strip() or "(empty)",
    ]
    return "\n\n".join(sections) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Render the body from ``PR_TITLE``/``PR_BODY`` into ``--out``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    title = os.environ.get("PR_TITLE", "").strip()
    body = os.environ.get("PR_BODY", "").replace("\r", "")
    if not title:
        print("ERROR: PR_TITLE is required", file=sys.stderr)
        return 2
    if MARKER in body:
        print("Body already rewritten; nothing to do.")
        return EXIT_ALREADY_REWRITTEN
    # LF on every platform: a CR in the body breaks CI's section matcher (#318).
    args.out.write_text(render(title, body), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
