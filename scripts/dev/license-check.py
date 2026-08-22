#!/usr/bin/env python3
"""Validate installed-package licenses against the hermes allowlist.

Reads pip-licenses JSON output on stdin. Exits 0 if every package's
license is in the allowlist; exits 1 with one line per violation if not.

## Why

Meta issue #71 Phase 0 blocking item. `pip-audit` catches CVEs but not
licenses. A GPL/AGPL dep would create commercial-use constraints
incompatible with hermes' private closed operation. This script is the
enforcement point in CI (see .github/workflows/ci.yml `licenses` job).

## Allowlist rationale

The allowlist covers permissive licenses only:

- **MIT / Apache-2.0 / BSD-*** — the modern permissive-OSS mainline
- **ISC** — MIT-equivalent, common in node/js ecosystem
- **Python-2.0 / Python Software Foundation License** — stdlib itself
- **MPL-2.0** — file-level copyleft, permissive for our use (linking
  MPL code into a proprietary app is explicitly permitted; only MPL
  files themselves are copyleft)

Explicitly blocked:

- **GPL-2.0 / GPL-3.0 / AGPL-*** — strong copyleft, would require open-
  sourcing hermes' source
- **LGPL-2.1 / LGPL-3.0** — weaker copyleft, but dynamic linking rules
  are ambiguous in the Python packaging story; safer to block

If a dep with a non-allowlisted license is genuinely required, add an
exception line + justification to `known_licenses.yaml` (parallel to
`known_advisories.yaml`) and re-run.

## Invocation

    uv tool install pip-licenses
    uv tool run pip-licenses --format=json | python3 scripts/dev/license-check.py

    # Self-test:
    HERMES_SELF_TEST=1 python3 scripts/dev/license-check.py

## Exit codes

- 0: every package's license is allowlisted (or explicitly excepted)
- 1: at least one violation
- 2: usage error / malformed stdin
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path

# ────────────────────────────────────────────────────────────────
# Allowlist. Match against normalized license identifier (SPDX-ish).
# Case-insensitive comparison.
# ────────────────────────────────────────────────────────────────
ALLOWED_LICENSES = frozenset(
    identifier.lower()
    for identifier in (
        # Permissive-OSS mainline
        "MIT",
        "MIT License",
        "MIT license (MIT)",
        "Apache 2.0",
        "Apache-2.0",
        "Apache Software License",
        "Apache Software License 2.0",
        "BSD",
        "BSD License",
        "BSD 2-Clause",
        "BSD-2-Clause",
        "BSD 3-Clause",
        "BSD-3-Clause",
        "BSD-3-Clause OR Apache-2.0",
        "ISC",
        "ISC License",
        "ISC License (ISCL)",
        # Python-specific
        "Python-2.0",
        "PSF-2.0",
        "Python Software Foundation License",
        # File-level copyleft (permissive for our use)
        "MPL-2.0",
        "Mozilla Public License 2.0 (MPL 2.0)",
        # Public domain / equivalent
        "Public Domain",
        "The Unlicense (Unlicense)",
        "Unlicense",
        "CC0-1.0",
    )
)


# ────────────────────────────────────────────────────────────────
# Package-name exemptions live in `known_licenses.yaml` at repo root.
# The file uses a simple YAML-adjacent format that this script parses
# line-by-line (no PyYAML dep — Phase 0 stays stdlib-only). See the
# file's own header for the format.
# ────────────────────────────────────────────────────────────────
def load_exceptions() -> set[str]:
    """Return set of package names allowlisted despite license mismatch."""
    exceptions_path = Path(__file__).resolve().parent.parent.parent / "known_licenses.yaml"
    if not exceptions_path.is_file():
        return set()
    exceptions: set[str] = set()
    with exceptions_path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("- package:"):
                name = line.split(":", 1)[1].strip().strip('"').strip("'")
                if name:
                    exceptions.add(name.lower())
    return exceptions


def is_allowed(license_str: str) -> bool:
    """Whether ``license_str`` matches the allowlist (case-insensitive)."""
    return license_str.strip().lower() in ALLOWED_LICENSES


def check_packages(pkgs: Iterable[dict]) -> list[str]:
    """Return newline-joinable list of violations. Empty = all-pass."""
    exceptions = load_exceptions()
    violations: list[str] = []
    for pkg in pkgs:
        name = pkg.get("Name") or pkg.get("name") or "<unknown>"
        license_str = pkg.get("License") or pkg.get("license") or "UNKNOWN"
        if name.lower() in exceptions:
            continue
        if is_allowed(license_str):
            continue
        violations.append(f"{name}: {license_str}")
    return violations


def self_test() -> int:
    """Sanity-check the allowlist logic against 4 canonical fixtures."""
    fail = 0

    # Fixture 1: MIT license → allowed
    v = check_packages([{"Name": "pytest", "License": "MIT"}])
    if v:
        print(f"SELF-TEST FAIL: MIT should pass, got: {v}", file=sys.stderr)
        fail = 1

    # Fixture 2: GPL-3 → violation
    v = check_packages([{"Name": "readline", "License": "GPL-3.0"}])
    if v != ["readline: GPL-3.0"]:
        print(f"SELF-TEST FAIL: GPL-3 should fail, got: {v}", file=sys.stderr)
        fail = 1

    # Fixture 3: mixed — Apache OK, AGPL fails
    v = check_packages(
        [
            {"Name": "grpcio", "License": "Apache-2.0"},
            {"Name": "some-agpl-lib", "License": "AGPL-3.0"},
        ]
    )
    if v != ["some-agpl-lib: AGPL-3.0"]:
        print(f"SELF-TEST FAIL: mixed AGPL should fail solo, got: {v}", file=sys.stderr)
        fail = 1

    # Fixture 4: unknown license → violation
    v = check_packages([{"Name": "obscure", "License": "UNKNOWN"}])
    if v != ["obscure: UNKNOWN"]:
        print(f"SELF-TEST FAIL: UNKNOWN should fail, got: {v}", file=sys.stderr)
        fail = 1

    if fail == 0:
        print("SELF-TEST PASS: license-check.py handles 4 fixtures correctly.")
    return fail


def main() -> int:
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return self_test()

    try:
        packages = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"ERROR: could not parse stdin as JSON: {e}", file=sys.stderr)
        print(
            "Expected: `uv tool run pip-licenses --format=json"
            " | python3 scripts/dev/license-check.py`",
            file=sys.stderr,
        )
        return 2

    if not isinstance(packages, list):
        print("ERROR: expected JSON array on stdin", file=sys.stderr)
        return 2

    violations = check_packages(packages)
    if not violations:
        print(f"License check: {len(packages)} packages, 0 violations.")
        return 0

    print("::error::License check failed — non-allowlisted licenses found:", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    print(
        "\nAdd exception to `known_licenses.yaml` if the license is acceptable for our use.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
