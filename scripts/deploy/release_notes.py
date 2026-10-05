#!/usr/bin/env python3
"""Build the release notes for one deploy of hermes (#626).

Lists the commits on main since the previous deploy, which are squash merges
named after their PR, and the ``changelog.d`` lines those commits added. The
previous deploy is the newest ``deploy-*`` tag reachable from the release's
parent, so a redeploy of a tagged commit still lists what that commit brought.
With no earlier tag, the notes cover the ``FIRST_DEPLOY_COMMITS`` latest
commits.

Usage::

    release_notes.py --release <sha> --repo <owner/name> --format markdown
    release_notes.py --release <sha> --repo <owner/name> --format telegram

``markdown`` is the GitHub release body; ``telegram`` is the plain-text
message the CD deploy job posts after a good deploy. Only the standard
library and ``git`` are used, so it runs before ``uv sync``.

Exit codes: 0 on success, 2 when ``git`` fails (unknown commit, no repo).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TAG_PREFIX = "deploy-"
FIRST_DEPLOY_COMMITS = 20
# Telegram caps a message at 4096 characters; a deploy rarely carries more.
TELEGRAM_MAX_ITEMS = 15
SECTIONS = ("added", "changed", "deprecated", "removed", "fixed", "security")
_FRAGMENT = re.compile(r"^changelog\.d/(?:\d+-|\+[a-z0-9._-]+\.)(?P<section>[a-z]+)\.md$")
_PR_SUFFIX = re.compile(r"\(#(?P<pr>\d+)\)$")


@dataclass(frozen=True)
class Change:
    """One commit on main: its subject and the changelog lines it added."""

    sha: str
    subject: str
    fragments: tuple[tuple[str, str], ...]

    @property
    def pr(self) -> int | None:
        """Return the PR number a squash-merge subject ends with, if any."""
        match = _PR_SUFFIX.search(self.subject)
        return int(match["pr"]) if match else None


@dataclass(frozen=True)
class Notes:
    """Everything a deploy's notes say."""

    release: str
    previous_tag: str | None
    changes: tuple[Change, ...]


def _git(root: Path, *args: str) -> str:
    """Run git in ``root`` and return its stdout."""
    return subprocess.run(  # noqa: S603 (fixed git argv, no shell)
        ["git", "-C", str(root), *args],  # noqa: S607 (git from PATH, as every CI step)
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def previous_tag(root: Path, release: str) -> str | None:
    """Return the newest deploy tag reachable from the release's parent, or None."""
    try:
        return _git(
            root, "describe", "--tags", "--abbrev=0", "--match", f"{TAG_PREFIX}*", f"{release}^"
        ).strip()
    except subprocess.CalledProcessError:
        return None


def _fragments(root: Path, sha: str) -> tuple[tuple[str, str], ...]:
    """Return (section, line) for every changelog fragment commit ``sha`` added."""
    added = _git(root, "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A", "-r", sha)
    found: list[tuple[str, str]] = []
    for path in added.splitlines():
        match = _FRAGMENT.match(path)
        if not match or match["section"] not in SECTIONS:
            continue
        found += [(match["section"], item) for item in _items(_git(root, "show", f"{sha}:{path}"))]
    return tuple(found)


def _items(fragment: str) -> list[str]:
    """Return the bullets of a fragment, joining each bullet's wrapped lines."""
    items: list[str] = []
    for line in fragment.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith(("- ", "* ")) or not items:
            items.append(text.removeprefix("- ").removeprefix("* ").strip())
        else:
            items[-1] = f"{items[-1]} {text}"
    return items


def collect(root: Path, release: str) -> Notes:
    """Read the commits and changelog lines between the previous deploy and ``release``."""
    tag = previous_tag(root, release)
    if tag:
        log = _git(root, "log", "--first-parent", "--format=%H%x1f%s", f"{tag}..{release}")
    else:
        log = _git(
            root, "log", "--first-parent", f"-n{FIRST_DEPLOY_COMMITS}", "--format=%H%x1f%s", release
        )
    changes = []
    for line in log.splitlines():
        sha, _, subject = line.partition("\x1f")
        changes.append(Change(sha=sha, subject=subject, fragments=_fragments(root, sha)))
    full = _git(root, "rev-parse", f"{release}^{{commit}}").strip()
    return Notes(release=full, previous_tag=tag, changes=tuple(changes))


def _since(notes: Notes) -> str:
    """Return the phrase naming what the list is measured from."""
    if notes.previous_tag:
        return f"since {notes.previous_tag}"
    return f"in the {FIRST_DEPLOY_COMMITS} latest commits (no earlier deploy tag)"


def render_markdown(notes: Notes, repo: str) -> str:
    """Return the GitHub release body."""
    lines = [f"Release `{notes.release[:12]}` ({notes.release}).", ""]
    if notes.previous_tag:
        compare = f"https://github.com/{repo}/compare/{notes.previous_tag}...{notes.release[:12]}"
        lines += [f"Previous deploy: `{notes.previous_tag}` ([diff]({compare})).", ""]
    if not notes.changes:
        lines.append("No new commits since the previous deploy: this is a redeploy.")
        return "\n".join(lines) + "\n"
    by_section: dict[str, list[str]] = {}
    for change in notes.changes:
        suffix = f" (#{change.pr})" if change.pr else ""
        for section, text in change.fragments:
            by_section.setdefault(section, []).append(f"- {text}{suffix}")
    if by_section:
        lines += ["## Changelog", ""]
        for section in SECTIONS:
            if section in by_section:
                lines += [f"### {section.capitalize()}", "", *by_section[section], ""]
    lines += [f"## Merged {_since(notes)} ({len(notes.changes)})", ""]
    lines += [f"- {change.subject}" for change in notes.changes]
    return "\n".join(lines) + "\n"


def render_telegram(notes: Notes, repo: str) -> str:
    """Return the plain-text deploy message."""
    sha12 = notes.release[:12]
    lines = [f"hermes {sha12} is live on hermes-01."]
    if not notes.changes:
        lines.append("Redeploy: no new commits since the previous deploy.")
    else:
        count = len(notes.changes)
        lines.append(f"{count} change{'s' if count != 1 else ''} {_since(notes)}:")
        lines += [f"- {change.subject}" for change in notes.changes[:TELEGRAM_MAX_ITEMS]]
        if count > TELEGRAM_MAX_ITEMS:
            lines.append(f"... and {count - TELEGRAM_MAX_ITEMS} more")
    lines.append(f"Notes: https://github.com/{repo}/releases/tag/{TAG_PREFIX}{sha12}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Print the notes for ``--release`` in the requested format."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", required=True, help="commit being deployed")
    parser.add_argument("--repo", required=True, help="owner/name, for links")
    parser.add_argument("--format", choices=("markdown", "telegram"), default="markdown")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="git checkout")
    args = parser.parse_args(argv)
    try:
        notes = collect(args.root, args.release)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    render = render_markdown if args.format == "markdown" else render_telegram
    sys.stdout.write(render(notes, args.repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
