#!/usr/bin/env python3
"""Enforce per-package coverage thresholds by reading coverage.json.

Usage:
  python scripts/dev/coverage-check.py [--bootstrap] [--report coverage.json]

--bootstrap: packages with 0 executable lines pass instead of fail.
"""

import argparse
import json
import sys
from pathlib import Path

THRESHOLDS = {
    "src/hermes/domain": 95,
    "src/hermes/application": 90,
    "src/hermes/adapters/ledger": 90,
    "src/hermes/adapters/exchanges": 85,
    "src/hermes/adapters/fx": 85,
    "src/hermes/adapters/healthchecks": 85,
    "src/hermes/adapters/market_data": 85,
    "src/hermes/adapters/notifier": 85,
    # Sentry integration lives in infrastructure/ (it is an SDK init
    # wrapper, not a hexagonal-port adapter — no adapters/sentry/ package).
    # Held at the same 85% adapter-tier bar because it carries wire-level
    # semantics (Sentry SDK init + capture wrappers).
    "src/hermes/infrastructure/sentry_integration": 85,
    "src/hermes/entrypoints": 70,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--report", default="coverage.json", type=Path)
    args = parser.parse_args()

    data = json.loads(args.report.read_text())
    files = data["files"]
    failures: list[str] = []

    for prefix, threshold in THRESHOLDS.items():
        matching = {p: f for p, f in files.items() if p.replace("\\", "/").startswith(prefix)}
        total_lines = sum(f["summary"]["num_statements"] for f in matching.values())
        if total_lines == 0:
            marker = "PASS (bootstrap)" if args.bootstrap else "FAIL"
            print(f"{prefix:45s} EMPTY — {marker}")
            if not args.bootstrap:
                failures.append(f"{prefix}: no executable lines")
            continue
        covered = sum(f["summary"]["covered_lines"] for f in matching.values())
        pct = 100 * covered / total_lines
        status = "PASS" if pct >= threshold else "FAIL"
        print(f"{prefix:45s} {pct:6.2f}% (threshold {threshold}%) — {status}")
        if pct < threshold:
            failures.append(f"{prefix}: {pct:.2f}% < {threshold}%")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
