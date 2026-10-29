#!/usr/bin/env python3
"""Split a cosmic-ray session into shards and give one verdict over them (#1213).

The Sunday mutation run plants about 1,100 bugs and each costs a full test
run, so one runner cannot finish them inside a job limit. Each matrix leg
runs ``cosmic-ray init`` on the same config, then ``select`` keeps its share
and marks the rest skipped, so ``cosmic-ray exec`` only runs that share.
The verdict job then reads every leg's session and prints PASS or FAIL.

Every leg runs its own ``init``, so job ids differ between sessions; a
mutant is matched across them by what it mutates (module, operator,
occurrence, position), never by job id.

Usage:
    mutation-shard.py select SESSION --shards N --index I
    mutation-shard.py verdict SESSION [SESSION ...] --floor PERCENT [--summary FILE]

Exit codes:
    0  select done, or verdict PASS
    1  verdict FAIL: a mutant no shard ran, or a kill rate below the floor
    2  usage error
"""

import argparse
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from cosmic_ray.work_db import WorkDB, use_db
from cosmic_ray.work_item import TestOutcome, WorkerOutcome, WorkItem, WorkResult

type MutantKey = tuple[str, str, int, int, int, int, int]


def mutant_key(item: WorkItem) -> MutantKey:
    """Return what a work item mutates, the same in every session of one config.

    Args:
        item: A work item holding exactly one mutation.

    Returns:
        ``(module, operator, occurrence, start row, start col, end row, end col)``.

    Raises:
        ValueError: The item holds no mutation or more than one.
    """
    if len(item.mutations) != 1:
        raise ValueError(
            f"work item {item.job_id} holds {len(item.mutations)} mutations, expected 1"
        )
    spec = item.mutations[0]
    return (
        spec.module_path.as_posix(),
        spec.operator_name,
        spec.occurrence,
        *spec.start_pos,
        *spec.end_pos,
    )


def shard_of(keys: Iterable[MutantKey], shards: int) -> dict[MutantKey, int]:
    """Deal mutants round-robin over ``shards`` in key order.

    Sorting first makes the split independent of database row order, and
    dealing round-robin spreads a heavy module over every shard.

    Args:
        keys: Every mutant of the session.
        shards: Number of shards, at least 1.

    Returns:
        The shard index of each mutant.

    Raises:
        ValueError: ``shards`` is below 1.
    """
    if shards < 1:
        raise ValueError(f"shards must be at least 1, got {shards}")
    return {key: rank % shards for rank, key in enumerate(sorted(set(keys)))}


def select(session: Path, shards: int, index: int) -> tuple[int, int]:
    """Mark every mutant outside shard ``index`` as skipped.

    Args:
        session: A session ``cosmic-ray init`` just created.
        shards: Number of shards.
        index: This leg's shard, ``0 <= index < shards``.

    Returns:
        ``(kept, total)`` mutant counts.

    Raises:
        ValueError: ``index`` is outside ``[0, shards)``.
    """
    if not 0 <= index < shards:
        raise ValueError(f"index must be in [0, {shards}), got {index}")
    with use_db(str(session), WorkDB.Mode.open) as db:
        items = list(db.work_items)
        assignment = shard_of((mutant_key(item) for item in items), shards)
        kept = 0
        for item in items:
            if assignment[mutant_key(item)] == index:
                kept += 1
            else:
                db.set_result(item.job_id, WorkResult(worker_outcome=WorkerOutcome.SKIPPED))
    return kept, len(items)


@dataclass(frozen=True, slots=True)
class ModuleCount:
    """Mutant counts of one module.

    Attributes:
        module: Module path.
        ran: Mutants some shard ran.
        survived: Run mutants no test caught.
        missing: Mutants no shard ran.
    """

    module: str
    ran: int
    survived: int
    missing: int


@dataclass(frozen=True, slots=True)
class Verdict:
    """The merged result of every shard.

    Attributes:
        total: Mutants in the config.
        killed: Mutants a test caught.
        survivors: Mutants no test caught, by key.
        missing: Mutants no shard ran, by key.
        floor: Minimum kill rate in percent.
        modules: Counts per module, by module path.
    """

    total: int
    killed: int
    survivors: tuple[MutantKey, ...]
    missing: tuple[MutantKey, ...]
    floor: float
    modules: tuple[ModuleCount, ...] = ()

    @property
    def ran(self) -> int:
        """Return how many mutants some shard ran."""
        return self.killed + len(self.survivors)

    @property
    def kill_rate(self) -> float:
        """Return the percentage of run mutants that were killed, 0 when none ran."""
        return 100 * self.killed / self.ran if self.ran else 0.0

    @property
    def passed(self) -> bool:
        """Return whether every mutant ran and the kill rate reaches the floor."""
        return not self.missing and self.ran > 0 and self.kill_rate >= self.floor


def merge(results: Iterable[tuple[MutantKey, WorkResult | None]], floor: float) -> Verdict:
    """Merge per-session results into one verdict.

    A skipped or absent result counts as not run. When two sessions both ran
    a mutant (two shards overlapping), a survival in either counts.

    Args:
        results: ``(key, result)`` for every work item of every session;
            ``None`` when the item has no result yet.
        floor: Minimum kill rate in percent.

    Returns:
        The verdict.
    """
    outcomes: dict[MutantKey, set[bool]] = defaultdict(set)
    for key, result in results:
        survived = outcomes[key]
        if result is not None and result.worker_outcome != WorkerOutcome.SKIPPED:
            survived.add(result.test_outcome == TestOutcome.SURVIVED)
    survivors = tuple(sorted(key for key, seen in outcomes.items() if True in seen))
    missing = tuple(sorted(key for key, seen in outcomes.items() if not seen))
    killed = sum(1 for seen in outcomes.values() if seen == {False})
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for key, seen in outcomes.items():
        count = counts[key[0]]
        count[0] += bool(seen)
        count[1] += True in seen
        count[2] += not seen
    modules = tuple(
        ModuleCount(module=module, ran=ran, survived=survived, missing=gone)
        for module, (ran, survived, gone) in sorted(counts.items())
    )
    return Verdict(
        total=len(outcomes),
        killed=killed,
        survivors=survivors,
        missing=missing,
        floor=floor,
        modules=modules,
    )


def _session_results(session: Path) -> list[tuple[MutantKey, WorkResult | None]]:
    with use_db(str(session), WorkDB.Mode.open) as db:
        done = {item.job_id: result for item, result in db.completed_work_items}
        return [(mutant_key(item), done.get(item.job_id)) for item in db.work_items]


def render(verdict: Verdict) -> str:
    """Return the Markdown summary: verdict line, per-module table, survivors.

    Args:
        verdict: The merged verdict.

    Returns:
        Markdown text.
    """
    word = "PASS" if verdict.passed else "FAIL"
    lines = [
        f"## Mutation run: {word}",
        "",
        f"Kill rate {verdict.kill_rate:.2f}% over {verdict.ran} of {verdict.total} mutants "
        f"(floor {verdict.floor:g}%).",
    ]
    if verdict.missing:
        lines.append(
            f"{len(verdict.missing)} mutants were not run by any shard, so the run is incomplete."
        )
    lines += ["", "| Module | Run | Survived | Not run |", "| --- | --- | --- | --- |"]
    lines += [f"| `{m.module}` | {m.ran} | {m.survived} | {m.missing} |" for m in verdict.modules]
    if verdict.survivors:
        lines += ["", "Survivors (module, operator, occurrence, line:col):", ""]
        lines += [
            f"- `{module}` {operator} #{occurrence} at {row}:{col}"
            for module, operator, occurrence, row, col, _end_row, _end_col in verdict.survivors
        ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``select`` or ``verdict``.

    Args:
        argv: Command-line arguments, ``sys.argv[1:]`` when ``None``.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("select", help="keep one shard of a fresh session")
    pick.add_argument("session", type=Path)
    pick.add_argument("--shards", type=int, required=True)
    pick.add_argument("--index", type=int, required=True)
    judge = commands.add_parser("verdict", help="merge shard sessions and print PASS or FAIL")
    judge.add_argument("sessions", type=Path, nargs="+")
    judge.add_argument("--floor", type=float, required=True)
    judge.add_argument("--summary", type=Path, help="append the Markdown summary to this file")
    args = parser.parse_args(argv)

    if args.command == "select":
        try:
            kept, total = select(args.session, args.shards, args.index)
        except ValueError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        print(f"shard {args.index + 1} of {args.shards}: {kept} of {total} mutants")
        return 0

    verdict = merge(
        (pair for session in args.sessions for pair in _session_results(session)), args.floor
    )
    text = render(verdict)
    print(text, end="")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(text)
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
