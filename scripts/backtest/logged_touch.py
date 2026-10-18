#!/usr/bin/env python3
"""Refuse to recover a candidate stage whose touch is already in the trial log (ADR-026 §3).

A recovery run exists for a job that died before it logged anything. A trial
line for the candidate, in this batch and this split, means the touch was
spent, so the dispatch stops and a person decides. The answer comes from the
log alone, never from a metric value.

Usage:
    python scripts/backtest/logged_touch.py --trial-log LOG --batch-id ID \
        --candidate STRATEGY_ID --stage train|validation

Exit codes:
    0 - nothing is logged; prints ``PASS no_logged_touch``
    1 - a touch is logged; prints ``FAIL no_logged_touch``
    3 - the trial log cannot be read
"""

import argparse
import json
import sys
from pathlib import Path

STAGES = ("train", "validation")


def is_logged(trial_log: Path, batch_id: str, candidate: str, stage: str) -> bool:
    """Return whether the log holds a ``stage`` trial of ``candidate`` in batch ``batch_id``."""
    for line in trial_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        trial = json.loads(line)
        if (
            trial.get("split") == stage
            and trial.get("strategy_id") == candidate
            and (trial.get("parameters") or {}).get("batch.id") == batch_id
        ):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and print the verdict."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trial-log", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--stage", choices=STAGES, required=True)
    args = parser.parse_args(argv)
    try:
        logged = is_logged(args.trial_log, args.batch_id, args.candidate, args.stage)
    except (OSError, json.JSONDecodeError, TypeError, AttributeError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 3
    print(f"{'FAIL' if logged else 'PASS'} no_logged_touch")
    return 1 if logged else 0


if __name__ == "__main__":
    sys.exit(main())
