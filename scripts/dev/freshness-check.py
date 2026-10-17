#!/usr/bin/env python3
"""Report pinned tool versions that Dependabot does not update and are behind.

Dependabot keeps ``uv.lock``, the GitHub Actions SHA pins, the pre-commit
hook revisions and the Docker base images current. Several pins live where
it cannot see them: ``ARG`` lines in the runner image, the VPS bootstrap
script and ``version:``/``==`` pins inside workflow steps. This script reads
each of those pins, asks its registry for the latest stable release and
prints a Markdown table of the ones that are behind (#906 follow-up).

It never changes a file. Exit code is 0 unless ``--strict`` is passed and
something is behind, or a registry cannot be reached.

Usage:
    python3 scripts/dev/freshness-check.py [--strict]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_S = 20


@dataclass(frozen=True)
class Pin:
    """One pinned version and where it lives."""

    tool: str
    current: str
    location: str


@dataclass(frozen=True)
class Source:
    """How to find a pin in the tree and where its latest release is published."""

    tool: str
    path: str
    pattern: str
    latest: Callable[[], str]


def _get_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.load(response)


def _field(data: object, *keys: str) -> str:
    for key in keys:
        if not isinstance(data, dict):
            raise TypeError(f"unexpected registry answer at {key!r}")
        data = data[key]
    return str(data)


def _pypi(name: str) -> Callable[[], str]:
    def latest() -> str:
        return _field(_get_json(f"https://pypi.org/pypi/{name}/json"), "info", "version")

    return latest


def _npm(name: str) -> Callable[[], str]:
    def latest() -> str:
        return _field(_get_json(f"https://registry.npmjs.org/{name}/latest"), "version")

    return latest


def _github(repo: str) -> Callable[[], str]:
    # Tags over git, not the REST API: no token and no API rate limit.
    def latest() -> str:
        refs = subprocess.run(  # noqa: S603 (fixed git argv, no shell)
            ["git", "ls-remote", "--tags", "--refs", f"https://github.com/{repo}"],  # noqa: S607 (git from PATH, as every CI step)
            capture_output=True,
            text=True,
            check=True,
            timeout=TIMEOUT_S,
        ).stdout
        return newest_stable(line.rsplit("/", 1)[-1] for line in refs.splitlines())

    return latest


def _python_patch(series: str) -> Callable[[], str]:
    def latest() -> str:
        data = _get_json("https://endoflife.date/api/python.json")
        for cycle in data if isinstance(data, list) else []:
            if isinstance(cycle, dict) and cycle.get("cycle") == series:
                return str(cycle["latest"])
        raise LookupError(f"Python {series} not listed")

    return latest


SOURCES: tuple[Source, ...] = (
    Source(
        "actions runner",
        "scripts/dev/gh-runner/Dockerfile",
        r"^ARG RUNNER_VERSION=(\S+)$",
        _github("actions/runner"),
    ),
    Source("uv", "scripts/dev/gh-runner/Dockerfile", r"^ARG UV_VERSION=(\S+)$", _pypi("uv")),
    Source(
        "markdownlint-cli2",
        "scripts/dev/gh-runner/Dockerfile",
        r"^ARG MARKDOWNLINT_VERSION=(\S+)$",
        _npm("markdownlint-cli2"),
    ),
    Source(
        "gitleaks",
        "scripts/dev/gh-runner/Dockerfile",
        r"^ARG GITLEAKS_VERSION=(\S+)$",
        _github("gitleaks/gitleaks"),
    ),
    Source("Bun", "scripts/dev/gh-runner/Dockerfile", r"^ARG BUN_VERSION=(\S+)$", _npm("bun")),
    Source(
        "Claude Code",
        "scripts/dev/gh-runner/Dockerfile",
        r"^ARG CLAUDE_CODE_VERSION=(\S+)$",
        _npm("@anthropic-ai/claude-code"),
    ),
    Source(
        "Python (VPS)",
        "scripts/deploy/vps_bootstrap.sh",
        r"^PYTHON_VERSION=(\S+)$",
        _python_patch("3.14"),
    ),
    Source("uv", "scripts/deploy/vps_bootstrap.sh", r"^UV_VERSION=(\S+)$", _pypi("uv")),
    Source(
        "uv",
        ".github/workflows/*.yml",
        r"setup-uv@.*\n\s*with: \{ version: \"([^\"]+)\" \}",
        _pypi("uv"),
    ),
    Source("semgrep", ".github/workflows/*.yml", r"semgrep==([0-9][^\s]*)", _pypi("semgrep")),
    Source(
        "markdownlint-cli2",
        ".github/workflows/*.yml",
        r"markdownlint-cli2@([0-9][^\s\"']*)",
        _npm("markdownlint-cli2"),
    ),
)


def find_pins(source: Source, root: Path = ROOT) -> list[Pin]:
    """Return every pin ``source`` matches, one per distinct (file, version)."""
    pins: dict[tuple[str, str], Pin] = {}
    for path in sorted(root.glob(source.path)):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(source.pattern, text, re.MULTILINE):
            location = str(path.relative_to(root))
            pins[location, match.group(1)] = Pin(source.tool, match.group(1), location)
    return list(pins.values())


def newest_stable(tags: Iterable[str]) -> str:
    """Highest plain ``X.Y.Z`` tag, ignoring prefixes like ``v`` and pre-releases."""
    stable = [m.group(1) for t in tags if (m := re.fullmatch(r"[a-z-]*v?(\d+\.\d+\.\d+)", t))]
    if not stable:
        raise LookupError("no stable release tag")
    return max(stable, key=version_key)


def version_key(version: str) -> tuple[int, ...]:
    """Numeric sort key: ``"2.1.112"`` → ``(2, 1, 112)``; non-numeric parts are dropped."""
    return tuple(int(part) for part in re.findall(r"\d+", version))


def behind(pins: Iterable[Pin], latest: dict[str, str]) -> list[tuple[Pin, str]]:
    """Return each pin older than the latest release of its tool."""
    return [
        (pin, latest[pin.tool])
        for pin in pins
        if version_key(pin.current) < version_key(latest[pin.tool])
    ]


def render(stale: list[tuple[Pin, str]]) -> str:
    """Markdown report of the stale pins."""
    if not stale:
        return "All pinned tools outside Dependabot are on their latest release.\n"
    lines = ["| Tool | Pinned | Latest | Where |", "| --- | --- | --- | --- |"]
    lines.extend(f"| {p.tool} | {p.current} | {new} | `{p.location}` |" for p, new in stale)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Print the report; with ``--strict`` exit 1 when something is behind."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="exit 1 when a pin is behind")
    args = parser.parse_args(argv)

    pins: list[Pin] = []
    latest: dict[str, str] = {}
    for source in SOURCES:
        found = find_pins(source)
        if found and source.tool not in latest:
            latest[source.tool] = source.latest()
        pins.extend(found)

    stale = behind(pins, latest)
    report = render(stale)
    sys.stdout.write(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("## Tool freshness\n\n" + report)
    for pin, new in stale:
        print(f"::warning file={pin.location}::{pin.tool} {pin.current} is behind {new}")
    return 1 if args.strict and stale else 0


if __name__ == "__main__":
    sys.exit(main())
