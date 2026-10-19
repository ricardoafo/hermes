#!/usr/bin/env python3
"""Re-chain a forked trial log's tail after a base log, changing no trial (issue #1294).

Two results PRs that both start from the same committed trial log fork its hash
chain: once one merges, the other's first new line points at the wrong
predecessor. This appends the fork's new trials after the base log through
``JsonlTrialLog.append``, so each trial's body stays byte for byte the same and
only ``prev_sha256`` and ``sha256`` are recomputed. Every trial then sits in one
chain exactly once (ADR-012 D-11).

It prints counts only, never a field value, so its output is safe to share
(ADR-023 §7).

Usage:
    python scripts/backtest/rechain_trials.py --base MAIN_LOG --fork FORKED_LOG --out NEW_LOG

Exit codes:
    0 - ``--out`` holds the base followed by the fork's new trials, chain verified
    1 - refused: the fork does not share a start with the base, already continues it,
        has a repair line in its tail, or a re-chained body differs
    3 - a log cannot be read, its chain is broken, or ``--out`` already exists
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from hermes.adapters.trial_log.jsonl_file import REPAIR_KIND, JsonlTrialLog
from hermes.domain.errors import TrialLogError

_CHAIN_KEYS = frozenset({"prev_sha256", "sha256"})


class RefusedError(Exception):
    """The two logs are not a fork this tool can join."""


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _body(raw: str) -> dict[str, Any]:
    line: dict[str, Any] = json.loads(raw)
    return {k: v for k, v in line.items() if k not in _CHAIN_KEYS}


def shared_prefix(base: list[str], fork: list[str]) -> int:
    """Return how many leading lines the two logs have in common, byte for byte."""
    shared = 0
    for ours, theirs in zip(base, fork, strict=False):
        if ours != theirs:
            break
        shared += 1
    return shared


def rechain(base: Path, fork: Path, out: Path) -> dict[str, int]:
    """Write ``base`` then the fork's new trials to ``out`` and return the line counts.

    Raises:
        RefusedError: The logs are not a fork with a common start, or a body changed.
        TrialLogError: A chain is broken or a log cannot be read.
        FileExistsError: ``out`` already exists.
    """
    if out.exists():
        raise FileExistsError(f"{out} already exists")
    fork_records = JsonlTrialLog(fork).records()
    JsonlTrialLog(base).records()
    base_lines, fork_lines = _lines(base), _lines(fork)
    shared = shared_prefix(base_lines, fork_lines)
    if shared == 0:
        raise RefusedError("the fork shares no first line with the base")
    if shared == len(base_lines):
        raise RefusedError("the fork already continues the base; use it as it is")
    tail = fork_lines[shared:]
    if not tail:
        raise RefusedError("the fork has no trials after the shared start")
    if any(_body(raw).get("kind") == REPAIR_KIND for raw in tail):
        raise RefusedError("the fork's tail holds a repair line")

    out.write_bytes(base.read_bytes())
    try:
        joined = JsonlTrialLog(out)
        for record in fork_records[len(fork_records) - len(tail) :]:
            joined.append(record)
        joined.records()
        appended = _lines(out)[len(base_lines) :]
        if [_body(raw) for raw in appended] != [_body(raw) for raw in tail]:
            raise RefusedError("a re-chained trial body differs from the fork's")
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return {
        "base": len(base_lines),
        "fork": len(fork_lines),
        "shared": shared,
        "rechained": len(tail),
        "out": len(base_lines) + len(tail),
    }


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, join the logs and print counts only."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--fork", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        counts = rechain(args.base, args.fork, args.out)
    except RefusedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    except (TrialLogError, OSError, ValueError) as exc:
        # The message names a path and a line number, never a field value.
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(
        f"base {counts['base']} lines, fork {counts['fork']} lines, "
        f"shared {counts['shared']}, re-chained {counts['rechained']}, "
        f"out {counts['out']} lines; chain verified, bodies identical"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
