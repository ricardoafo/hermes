#!/usr/bin/env python3
"""Describe the version release a deploy carries, for CD to tag (ADR-022 §3).

Reads ``version`` from ``pyproject.toml`` at the checked-out release commit,
names it in SemVer (``1.0.0a1`` is ``1.0.0-shadow.1``) and takes that
release's section from ``CHANGELOG.md``. Prints one JSON object:

- ``{"tag": "v1.0.0-shadow.1", "name": "hermes v1.0.0-shadow.1", "body": ...}``
  when the version has a dated changelog section, so it was cut;
- ``{}`` otherwise (an uncut version such as ``0.0.0``), and CD tags nothing.

The CD deploy job creates the tag and release only if the tag does not exist
yet, so the first deploy of a version tags it and later deploys leave it.

Usage::

    version_release.py [--root <checkout>]

Only the standard library is used, so it runs before ``uv sync``; the version
mapping is loaded by path from ``src/hermes/release_version.py``.

Exit codes: 0 always (a problem prints ``{}`` and a warning on stderr), 2 usage.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

_PYPROJECT_VERSION_RE = re.compile(r'^version = "(?P<v>[^"]+)"$', re.MULTILINE)


def _load_release_version(root: Path) -> ModuleType:
    path = root / "src" / "hermes" / "release_version.py"
    spec = importlib.util.spec_from_file_location("hermes_release_version", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def describe(root: Path) -> dict[str, str]:
    """Return the tag, name and body for the version at ``root``, or ``{}``.

    Raises:
        FileNotFoundError: ``pyproject.toml``, ``CHANGELOG.md`` or the mapping module is missing.
        ValueError: ``pyproject.toml`` has no version, or it is not a hermes package version.
    """
    release_version = _load_release_version(root)
    match = _PYPROJECT_VERSION_RE.search((root / "pyproject.toml").read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("pyproject.toml has no version line")
    release = release_version.to_semver(match.group("v"))
    body = release_version.changelog_section(
        (root / "CHANGELOG.md").read_text(encoding="utf-8"), release
    )
    if body is None:
        return {}
    return {"tag": f"v{release}", "name": f"hermes v{release}", "body": body}


def main(argv: list[str] | None = None) -> int:
    """Print the version release as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="release checkout")
    args = parser.parse_args(argv)
    try:
        described = describe(args.root)
    except (OSError, ImportError, ValueError) as exc:
        print(f"version release not described: {exc}", file=sys.stderr)
        described = {}
    sys.stdout.write(json.dumps(described) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
