#!/usr/bin/env python3
"""Compute the version a push to main ships, and whether it ships (ADR-030).

The last release is the highest ``v0.Y.Z`` (or later ``vX.Y.Z``) tag merged
into the release commit. Against that tag's commit:

- the push **ships** when any file the release bundle carries changed
  (``BUNDLE_PATHS``); a push that changes only docs, tests or CI ships nothing;
- the bump is **MINOR** when a changelog fragment added or edited since the
  tag is ``added``, ``changed``, ``deprecated`` or ``removed``, or when any
  engine file changed (the ADR-025 list, ``ENGINE_PATHS``); **PATCH**
  otherwise (ADR-027 §1).

A computed bump never makes a MAJOR: the step to ``1.0.0`` stays a manual
release PR (ADR-027 §3). With no release tag yet, the version in
``pyproject.toml`` at the release is taken as is and the push ships.

Prints one JSON object::

    {"ships": true, "previous": "0.2.0", "version": "0.3.0", "bump": "minor",
     "tag": "v0.3.0", "engine_files": [...], "minor_fragments": [...]}

When the push ships nothing, ``version`` is the previous one and ``bump`` is
``none``. ``--stamp`` also writes ``version`` into ``pyproject.toml`` and
``src/hermes/__init__.py`` under ``--root``, for the wheel CD builds; the
repository copy keeps the last released version.

``--set <version>`` only stamps that version, for a build that was handed
it (the reusable wheel build checks out one commit, without tags).

``--settle <file>`` recomputes the version under the deploy lock against the
JSON the notes job wrote to ``<file>`` (ADR-030 §4). When an earlier deploy
took that number, it rewrites ``<file>`` and stamps the new version; the
printed JSON carries ``"moved": true`` so CD rebuilds the wheel and bundle.

Usage::

    next_version.py --release <sha> [--root <checkout>] [--stamp]
    next_version.py --release <sha> --settle <file> [--root <checkout>]
    next_version.py --set <version> [--root <checkout>]

Only the standard library and ``git`` are used, so it runs before ``uv sync``.

Exit codes: 0 success, 2 usage or git failure (unknown commit, no repo).
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Every repository path the CD bundle job copies into the release, plus the
# package sources and the lock the wheel and requirements come from. A change
# anywhere else cannot change what runs on hermes-01.
BUNDLE_PATHS = re.compile(
    r"^(src/hermes/|pyproject\.toml$|uv\.lock$|config/|deploy/|scripts/deploy/"
    r"|docs/experiments/swing-mvp1/(pre-registration-v1\.md|fee-history\.csv)$)"
)
# The same list as ENGINE_PATHS in scripts/dev/train-decision-replay.sh (ADR-025).
ENGINE_PATHS = re.compile(
    r"^src/hermes/(domain|application)/|^src/hermes/adapters/(fills|fees)/"
    r"|^config/strategies\.yaml$|^docs/experiments/[^/]+/(fee-history\.csv|data-manifest\.json)$"
    r"|^uv\.lock$"
)
MINOR_SECTIONS = frozenset({"added", "changed", "deprecated", "removed"})
_FRAGMENT = re.compile(r"^changelog\.d/(?:\d+-|\+[a-z0-9._-]+\.)(?P<section>[a-z]+)\.md$")
_RELEASE_TAG = re.compile(r"^v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")
_PYPROJECT_VERSION = re.compile(r'^version = "(?P<v>[^"]+)"$', re.MULTILINE)
_INIT_VERSION = re.compile(r'^__version__ = "(?P<v>[^"]+)"$', re.MULTILINE)


def _git(root: Path, *args: str) -> str:
    """Run git in ``root`` and return its stdout."""
    return subprocess.run(  # noqa: S603 (fixed git argv, no shell)
        ["git", "-C", str(root), *args],  # noqa: S607 (git from PATH, as every CI step)
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def last_release(root: Path, release: str) -> tuple[str, tuple[int, int, int]] | None:
    """Return the highest release tag merged into ``release`` and its numbers, or None."""
    found = []
    for tag in _git(root, "tag", "--list", "v*", "--merged", release).split():
        match = _RELEASE_TAG.match(tag)
        if match:
            found.append((tag, (int(match["major"]), int(match["minor"]), int(match["patch"]))))
    return max(found, key=lambda item: item[1]) if found else None


def bump(previous: tuple[int, int, int], kind: str) -> tuple[int, int, int]:
    """Return ``previous`` raised by a ``minor`` or ``patch`` bump."""
    major, minor, patch = previous
    if kind == "minor":
        return (major, minor + 1, 0)
    if kind == "patch":
        return (major, minor, patch + 1)
    raise ValueError(f"unknown bump {kind!r}")


def compute(root: Path, release: str) -> dict[str, object]:
    """Return the ship decision and version for ``release`` (see the module docstring).

    Raises:
        subprocess.CalledProcessError: git fails (unknown commit, no repo).
        ValueError: there is no release tag and ``pyproject.toml`` has no version.
    """
    full = _git(root, "rev-parse", f"{release}^{{commit}}").strip()
    found = last_release(root, full)
    if found is None:
        match = _PYPROJECT_VERSION.search(_git(root, "show", f"{full}:pyproject.toml"))
        if match is None:
            raise ValueError("no release tag and no version in pyproject.toml")
        version = match["v"]
        return {
            "ships": True,
            "previous": None,
            "version": version,
            "bump": "none",
            "tag": f"v{version}",
            "engine_files": [],
            "minor_fragments": [],
        }
    tag, numbers = found
    changed = _git(root, "diff", "--name-only", "--no-renames", f"{tag}^{{commit}}", full).split()
    touched = _git(
        root, "diff", "--name-only", "--diff-filter=AM", "--no-renames", f"{tag}^{{commit}}", full
    ).split()
    engine_files = sorted(path for path in changed if ENGINE_PATHS.search(path))
    minor_fragments = sorted(
        path
        for path in touched
        if (match := _FRAGMENT.match(path)) and match["section"] in MINOR_SECTIONS
    )
    previous = ".".join(map(str, numbers))
    if not any(BUNDLE_PATHS.search(path) for path in changed):
        return {
            "ships": False,
            "previous": previous,
            "version": previous,
            "bump": "none",
            "tag": tag,
            "engine_files": engine_files,
            "minor_fragments": minor_fragments,
        }
    kind = "minor" if engine_files or minor_fragments else "patch"
    version = ".".join(map(str, bump(numbers, kind)))
    return {
        "ships": True,
        "previous": previous,
        "version": version,
        "bump": kind,
        "tag": f"v{version}",
        "engine_files": engine_files,
        "minor_fragments": minor_fragments,
    }


def stamp(root: Path, version: str) -> None:
    """Write ``version`` into ``pyproject.toml`` and ``src/hermes/__init__.py`` under ``root``.

    Raises:
        ValueError: either file has no version line to replace.
    """
    for path, pattern, line in (
        (root / "pyproject.toml", _PYPROJECT_VERSION, f'version = "{version}"'),
        (root / "src" / "hermes" / "__init__.py", _INIT_VERSION, f'__version__ = "{version}"'),
    ):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text) is None:
            raise ValueError(f"{path} has no version line")
        path.write_text(pattern.sub(line, text, count=1), encoding="utf-8")


def settle(root: Path, release: str, built: Path) -> dict[str, object]:
    """Recompute ``release`` against the version in ``built``, adopting a moved one.

    Returns the current result with ``moved`` set. When the version moved,
    ``built`` is rewritten with the result and the tree under ``root`` stamped.

    Raises:
        subprocess.CalledProcessError: git fails (unknown commit, no repo).
        ValueError: as ``compute``, or ``built`` holds no version.
        OSError: ``built`` cannot be read or written.
    """
    before = json.loads(built.read_text(encoding="utf-8")).get("version")
    if not isinstance(before, str):
        raise ValueError(f"{built} has no version")
    result = compute(root, release)
    moved = result["version"] != before
    if moved:
        built.write_text(json.dumps(result) + "\n", encoding="utf-8")
        stamp(root, str(result["version"]))
    return {**result, "moved": moved}


def main(argv: list[str] | None = None) -> int:
    """Print the ship decision and version as JSON, and stamp it with ``--stamp``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", help="commit being deployed")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="release checkout")
    parser.add_argument("--stamp", action="store_true", help="write the version into the tree")
    parser.add_argument("--set", metavar="VERSION", help="only stamp VERSION, computing nothing")
    parser.add_argument("--settle", type=Path, metavar="FILE", help="adopt a version that moved")
    args = parser.parse_args(argv)
    if args.set:
        try:
            stamp(args.root, args.set)
        except (ValueError, OSError) as exc:
            print(f"version not stamped: {exc}", file=sys.stderr)
            return 2
        return 0
    if not args.release:
        parser.error("--release is required unless --set is given")
    try:
        if args.settle:
            result = settle(args.root, args.release, args.settle)
        else:
            result = compute(args.root, args.release)
        if args.stamp and not args.settle:
            stamp(args.root, str(result["version"]))
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else exc
        print(f"next version not computed: {detail}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
