#!/usr/bin/env python3
"""Refuse a Validation touch that would pass an idea family's budget (ADR-026 §3).

A touch is one Validation run of one strategy: the distinct
``(strategy_id, batch.id or "-", git_sha)`` triples among the trial log's
``split: validation`` lines. The commit separates the several runs of a
strategy that predates batches. A family's touches are those of every strategy that
belongs to it: the candidates of every batch in the experiment, plus the strategies that predate
batches (``LEGACY_FAMILIES``). The count comes from the log alone, never from a
metric value.

Usage:
    python scripts/backtest/validation_budget.py --trial-log LOG --batch FILE \
        --candidate STRATEGY_ID [--limit 5]

Exit codes:
    0 - the touch fits the budget; prints ``PASS validation_budget``
    1 - the family already used its budget; prints ``FAIL validation_budget``
    3 - the batch, the candidate or the trial log cannot be read
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# Strategies signed before batches existed, all in the 4h Donchian breakout family (ADR-026 §1).
LEGACY_FAMILIES = {
    "swing-donchian-4h": "donchian-breakout-4h",  # v1
    "swing-mvp1-v2": "donchian-breakout-4h",
    "swing-b1-trend-slope": "donchian-breakout-4h",
}
DEFAULT_LIMIT = 5
_FRONT_MATTER_FENCE = "---"


def read_front_matter(path: Path) -> dict[str, Any]:
    """Return a batch document's YAML front matter."""
    text = path.read_text(encoding="utf-8")
    parts = text.split(_FRONT_MATTER_FENCE, 2)
    if len(parts) < 3 or parts[0].strip():
        raise ValueError(f"{path} has no YAML front matter")
    loaded = yaml.safe_load(parts[1])
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} front matter is not a mapping")
    return loaded


def family_by_strategy(batch: Path) -> dict[str, str]:
    """Map every strategy id to its idea family (the experiment batches plus the legacy list)."""
    families = dict(LEGACY_FAMILIES)
    for sibling in {batch, *batch.parent.glob("*.md")}:
        for candidate in read_front_matter(sibling).get("candidates", []):
            families[str(candidate["strategy_id"])] = str(candidate["idea_family"])
    return families


def touches(trial_log: Path, strategy_ids: set[str]) -> int:
    """Count the distinct Validation touches the log holds for ``strategy_ids``."""
    seen: set[tuple[str, object, object]] = set()
    for line in trial_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        trial = json.loads(line)
        if trial.get("split") != "validation" or trial.get("strategy_id") not in strategy_ids:
            continue
        batch_id = (trial.get("parameters") or {}).get("batch.id") or "-"
        seen.add((trial["strategy_id"], batch_id, trial.get("git_sha")))
    return len(seen)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and print the budget verdict."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trial-log", type=Path, required=True)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)
    try:
        families = family_by_strategy(args.batch)
        family = families.get(args.candidate)
        if family is None:
            raise ValueError(f"{args.candidate} is in no batch of {args.batch.parent}")
        members = {strategy for strategy, name in families.items() if name == family}
        used = touches(args.trial_log, members)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, json.JSONDecodeError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 3
    verdict = "PASS" if used < args.limit else "FAIL"
    print(f"{verdict} validation_budget")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
