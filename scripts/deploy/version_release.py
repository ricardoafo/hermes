#!/usr/bin/env python3
"""Describe the version release a deploy carries, for CD to tag (ADR-022 §3).

Reads ``version`` from ``pyproject.toml`` at the checked-out release commit,
names it in SemVer (``1.0.0a1`` is ``1.0.0-shadow.1``) and takes that
release's section from ``CHANGELOG.md``. Prints one JSON object:

- ``{"tag": "v1.0.0-shadow.1", "name": ..., "body": ..., "target": <sha>}``
  when the version has a dated changelog section, so it was cut;
- ``{}`` otherwise (an uncut version such as ``0.0.0``), and CD tags nothing.

``target`` is the commit that first shipped the version, so the tag answers
"what ran as this version" once: the oldest first-parent commit from the cut
to the release that carries a ``deploy-<sha12>`` tag, or the release itself
when none does yet (it is deploying now). A version deployed before CD could
tag it is so tagged later on the commit that first shipped it.

The CD deploy job creates the tag and release only if the tag does not exist
yet, so the first deploy of a version tags it and later deploys leave it.

Usage::

    version_release.py --release <sha> [--root <checkout>]

Only the standard library and ``git`` are used, so it runs before ``uv sync``;
the version mapping is loaded by path from ``src/hermes/release_version.py``.

Exit codes: 0 always (a problem prints ``{}`` and a warning on stderr), 2 usage.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_PYPROJECT_VERSION_RE = re.compile(r'^version = "(?P<v>[^"]+)"$', re.MULTILINE)
DEPLOY_TAG_PREFIX = "deploy-"


def _git(root: Path, *args: str) -> str:
    """Run git in ``root`` and return its stdout."""
    return subprocess.run(  # noqa: S603 (fixed git argv, no shell)
        ["git", "-C", str(root), *args],  # noqa: S607 (git from PATH, as every CI step)
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def first_shipped(root: Path, release: str, package_version: str) -> str:
    """Return the oldest deployed commit carrying ``package_version``, else ``release``.

    Walks ``release``'s first-parent history back to the commit that set the
    version (the cut) and picks the oldest one with a ``deploy-<sha12>`` tag.
    """
    full = _git(root, "rev-parse", release).strip()
    line = f'^version = "{re.escape(package_version)}"$'
    cut = _git(
        root, "log", "--first-parent", "--format=%H", "-G", line, full, "--", "pyproject.toml"
    ).split()
    if not cut:
        return full
    deployed = {
        tag.removeprefix(DEPLOY_TAG_PREFIX)
        for tag in _git(root, "tag", "--list", f"{DEPLOY_TAG_PREFIX}*", "--merged", full).split()
    }
    history = _git(root, "rev-list", "--first-parent", "--reverse", full).split()
    since_cut = history[history.index(cut[-1]) :] if cut[-1] in history else [full]
    return next((sha for sha in since_cut if sha[:12] in deployed), full)


def _load_release_version(root: Path) -> ModuleType:
    path = root / "src" / "hermes" / "release_version.py"
    spec = importlib.util.spec_from_file_location("hermes_release_version", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def describe(root: Path, release: str) -> dict[str, str]:
    """Return the tag, name, body and target for the version at ``root``, or ``{}``.

    Raises:
        FileNotFoundError: ``pyproject.toml``, ``CHANGELOG.md`` or the mapping module is missing.
        ValueError: ``pyproject.toml`` has no version, or it is not a hermes package version.
        subprocess.CalledProcessError: git fails (unknown commit, no repo).
    """
    release_version = _load_release_version(root)
    match = _PYPROJECT_VERSION_RE.search((root / "pyproject.toml").read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("pyproject.toml has no version line")
    name = release_version.to_semver(match.group("v"))
    body = release_version.changelog_section(
        (root / "CHANGELOG.md").read_text(encoding="utf-8"), name
    )
    if body is None:
        return {}
    return {
        "tag": f"v{name}",
        "name": f"hermes v{name}",
        "body": body,
        "target": first_shipped(root, release, match.group("v")),
    }


def main(argv: list[str] | None = None) -> int:
    """Print the version release as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", required=True, help="commit being deployed")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="release checkout")
    args = parser.parse_args(argv)
    try:
        described = describe(args.root, args.release)
    except (OSError, ImportError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"version release not described: {exc}", file=sys.stderr)
        described = {}
    sys.stdout.write(json.dumps(described) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
