#!/usr/bin/env python3
"""Verify every package in uv.lock has a hash pinned.

`uv sync --frozen` verifies hashes at install time, but nothing today
verifies the lockfile ITSELF has hashes for every entry. If a package
lands in `uv.lock` without a hash (e.g., from a broken merge, a
manually-edited lockfile, or a tooling regression), `uv sync` would
silently accept an unverified install.

This script closes that gap. Runs in CI as a defense-in-depth check
alongside `pip-audit` (CVE scanning).

## Why

Meta issue #71 Phase 0 blocking item ("Reproducible builds via uv hash
locking"). The `uv.lock` file is committed per ADR-010 §1 for exactly
this reason — but nothing checks the lockfile's integrity.

## What it accepts

Each `[[package]]` entry in `uv.lock` must have EITHER:

- `wheels = [ { ... hash = "sha256:..." } ]`, OR
- `sdist = { ... hash = "sha256:..." }`, OR
- both (which is the common case)

**Exception**: the workspace root (`source = { editable = "." }`) is
skipped — that's hermes itself, not on PyPI. Also `virtual` sources
are skipped (same reason).

## Invocation

    python3 scripts/dev/reproducibility-check.py           # against uv.lock in cwd
    python3 scripts/dev/reproducibility-check.py path.lock # explicit path
    HERMES_SELF_TEST=1 python3 scripts/dev/reproducibility-check.py  # sanity

## Exit codes

- 0: every non-workspace package has a hash
- 1: at least one package lacks a hash
- 2: usage error / uv.lock not found / not valid TOML
"""

import os
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path

WORKSPACE_SOURCE_KINDS = frozenset({"editable", "virtual"})


def package_has_hash(pkg: dict) -> bool:
    """True if the package has at least one hash-pinned artifact."""
    # Skip workspace / virtual packages — they aren't from a registry
    source = pkg.get("source", {})
    if any(key in source for key in WORKSPACE_SOURCE_KINDS):
        return True  # exempt, not a violation

    # sdist hash
    sdist = pkg.get("sdist")
    if isinstance(sdist, dict) and sdist.get("hash"):
        return True

    # wheels[].hash — any wheel with a hash suffices
    wheels = pkg.get("wheels", [])
    if isinstance(wheels, list):
        for wheel in wheels:
            if isinstance(wheel, dict) and wheel.get("hash"):
                return True

    return False


def check_lockfile(packages: Iterable[dict]) -> list[str]:
    """Return newline-joinable list of `<name>@<version>` for violations."""
    violations: list[str] = []
    for pkg in packages:
        if not package_has_hash(pkg):
            name = pkg.get("name", "<unknown>")
            version = pkg.get("version", "<unknown>")
            violations.append(f"{name}@{version}")
    return violations


def self_test() -> int:
    """4 canonical fixtures. Returns 0 on all-pass, 1 on any failure."""
    fail = 0

    # Fixture 1: package with sdist hash → pass
    v = check_lockfile(
        [
            {
                "name": "pytest",
                "version": "9.0.0",
                "source": {"registry": "https://pypi.org/simple"},
                "sdist": {"url": "…", "hash": "sha256:abc"},
            }
        ]
    )
    if v:
        print(f"SELF-TEST FAIL: sdist-with-hash should pass, got: {v}", file=sys.stderr)
        fail = 1

    # Fixture 2: package with wheels hash but no sdist → pass
    v = check_lockfile(
        [
            {
                "name": "wheel-only",
                "version": "1.0.0",
                "source": {"registry": "https://pypi.org/simple"},
                "wheels": [{"url": "…", "hash": "sha256:def"}],
            }
        ]
    )
    if v:
        print(f"SELF-TEST FAIL: wheel-with-hash should pass, got: {v}", file=sys.stderr)
        fail = 1

    # Fixture 3: package with sdist but NO hash → fail
    v = check_lockfile(
        [
            {
                "name": "unhashed",
                "version": "0.1.0",
                "source": {"registry": "https://pypi.org/simple"},
                "sdist": {"url": "…"},  # no hash
            }
        ]
    )
    if v != ["unhashed@0.1.0"]:
        print(f"SELF-TEST FAIL: missing-hash should fail, got: {v}", file=sys.stderr)
        fail = 1

    # Fixture 4: workspace root (editable) → exempt
    v = check_lockfile(
        [
            {
                "name": "hermes",
                "version": "0.0.0",
                "source": {"editable": "."},
            }
        ]
    )
    if v:
        print(f"SELF-TEST FAIL: editable-workspace should be exempt, got: {v}", file=sys.stderr)
        fail = 1

    if fail == 0:
        print("SELF-TEST PASS: reproducibility-check.py handles 4 fixtures correctly.")
    return fail


def main() -> int:
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return self_test()

    lockfile = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("uv.lock")
    if not lockfile.is_file():
        print(
            f"ERROR: {lockfile} not found. Run from repo root or pass an explicit path.",
            file=sys.stderr,
        )
        return 2

    try:
        with lockfile.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        print(f"ERROR: {lockfile} is not valid TOML: {e}", file=sys.stderr)
        return 2

    packages = data.get("package", [])
    if not isinstance(packages, list):
        print(f"ERROR: {lockfile} has no [[package]] entries", file=sys.stderr)
        return 2

    violations = check_lockfile(packages)
    if not violations:
        print(f"Reproducibility check: {len(packages)} packages, 0 unhashed.")
        return 0

    print("::error::Reproducibility check failed — packages without hashes:", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    print(
        "\nRun `uv lock --refresh` to regenerate the lockfile with hashes.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
