#!/usr/bin/env python3
"""Refuse a Validation touch that would pass an idea family's budget (ADR-026 §3).

A touch is one Validation run of one strategy: the distinct
``(strategy_id, batch.id or "-", git_sha)`` triples among the trial log's
``split: validation`` lines. The commit separates the several runs of a
strategy that predates batches. A family's touches are those of every strategy that
belongs to it: the candidates of every batch in the experiment, plus the strategies that predate
batches (``LEGACY_FAMILIES``). A sweep's candidates are its grid points
(ADR-031). The count comes from the log alone, never from a metric value.

``--winners`` is a sweep's selection step (ADR-031 §3): it reads the family
winners ``hermes funnel --batch`` wrote after Train, drops each one whose
family has no touch left, and writes the ids sent to Validation, with the
dropped ones, to ``--sent-out``. Ids only.

Usage:
    python scripts/backtest/validation_budget.py --trial-log LOG --batch FILE \
        --candidate STRATEGY_ID [--limit 5]
    python scripts/backtest/validation_budget.py --trial-log LOG --batch FILE \
        --winners validation-candidates.json --sent-out sweep-selection.json [--limit 5]

Exit codes:
    0 - the touch fits the budget; prints ``PASS validation_budget``. With
        ``--winners``: the selection record is written, whatever it holds
    1 - the family already used its budget; prints ``FAIL validation_budget``
    3 - the batch, the candidate, the winners or the trial log cannot be read
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from hermes.application.calibration.research_batch import BatchError
from hermes.application.calibration.research_sweep import is_sweep, parse_sweep

# Strategies signed before batches existed, all in the 4h Donchian breakout family (ADR-026 §1).
LEGACY_FAMILIES = {
    "swing-donchian-4h": "donchian-breakout-4h",  # v1
    "swing-mvp1-v2": "donchian-breakout-4h",
    "swing-b1-trend-slope": "donchian-breakout-4h",
}
DEFAULT_LIMIT = 5
_READ_ERRORS = (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, json.JSONDecodeError)
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
        text = sibling.read_text(encoding="utf-8")
        if is_sweep(text):
            try:
                points = parse_sweep(sibling.as_posix(), text).points
            except BatchError as err:
                raise ValueError(f"{sibling}: {err}") from err
            families.update((p.candidate_id, p.family) for p in points)
            continue
        for candidate in read_front_matter(sibling).get("candidates", []):
            families[str(candidate["strategy_id"])] = str(candidate["idea_family"])
    return families


def family_touches(trial_log: Path, families: dict[str, str], strategy_id: str) -> int:
    """Count the Validation touches ``strategy_id``'s family has used.

    Raises:
        ValueError: ``strategy_id`` is in no batch of the experiment.
    """
    family = families.get(strategy_id)
    if family is None:
        raise ValueError(f"{strategy_id} is in no batch of the experiment")
    return touches(trial_log, {s for s, name in families.items() if name == family})


def send(
    trial_log: Path, batch: Path, winners: Sequence[str], limit: int
) -> tuple[list[str], list[str]]:
    """Split a sweep's family winners into those sent to Validation and those over budget."""
    families = family_by_strategy(batch)
    sent: list[str] = []
    over: list[str] = []
    for winner in winners:
        (sent if family_touches(trial_log, families, winner) < limit else over).append(winner)
    return sent, over


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
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--candidate")
    which.add_argument("--winners", type=Path)
    parser.add_argument("--sent-out", type=Path)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)
    if (args.winners is None) != (args.sent_out is None):
        parser.error("--winners and --sent-out go together")
    if args.winners is not None:
        return _select(args)
    try:
        used = family_touches(args.trial_log, family_by_strategy(args.batch), args.candidate)
    except _READ_ERRORS as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 3
    verdict = "PASS" if used < args.limit else "FAIL"
    print(f"{verdict} validation_budget")
    return 0 if verdict == "PASS" else 1


def _select(args: argparse.Namespace) -> int:
    try:
        listed = json.loads(args.winners.read_text(encoding="utf-8"))
        winners = [str(w) for w in listed["candidates"]]
        sent, over = send(args.trial_log, args.batch, winners, args.limit)
        record = {
            "schema": "hermes.sweep-selection/1",
            "winners": winners,
            "sent": sent,
            "over_budget": over,
            "limit": args.limit,
        }
        args.sent_out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except _READ_ERRORS as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 3
    for winner in winners:
        print(f"{'PASS' if winner in sent else 'FAIL'} {winner} validation_budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
