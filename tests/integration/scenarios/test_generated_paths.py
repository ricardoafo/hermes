"""Seeded market paths through the real serve, with every rule checked after every bar.

How does Hermes fail? Each seed builds a month of invented bars on three pairs
with outages, late and repeated bars and restarts (:mod:`.paths`), runs the
production ``hermes serve`` on the fake market over it, and after each bar
holds the ledger to the pre-registration rules (:mod:`.oracle`). The same path
is then run straight through, without its restarts, and the two ledgers must
match: a restart changes nothing.

Seeds: by default ten random-walk and ten hater seeds, 0 to 9, about 3 s of
CPU each (two runs of a 30-day path). ``HERMES_SCENARIO_SEEDS`` changes them
for a longer run:

- ``N`` runs seeds 0 to N-1 of each generator;
- ``N@START`` runs seeds START to START+N-1, so a nightly job can pass a new
  start each night (``200@20261007``) and the failing seed is in the test id.

A failure prints the seed, the bar and the rule, then what the path scripted.
Rerun one seed by its test id, which ends in ``[hater-7]`` or ``[walk-7]``,
with the same ``HERMES_SCENARIO_SEEDS`` when the seed is outside 0 to 9.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.integration.scenarios import paths
from tests.integration.scenarios.oracle import (
    MARKS_AFTER_BOOT,
    Oracle,
    differences,
    fingerprint,
    run_path,
)

pytestmark = pytest.mark.integration

SEEDS_ENV = "HERMES_SCENARIO_SEEDS"
_DEFAULT_SEEDS = 10

KNOWN_FAILURES: dict[tuple[str, int], str] = {}
"""Seeds that hit a filed defect, with the reason; each becomes a strict xfail."""


def seeds(spec: str | None) -> range:
    """Return the seeds ``HERMES_SCENARIO_SEEDS`` asks for: ``N`` or ``N@START``.

    Raises:
        ValueError: The value is neither form.

    """
    if not spec:
        return range(_DEFAULT_SEEDS)
    count, _, start = spec.partition("@")
    first = int(start) if start else 0
    return range(first, first + int(count))


GENERATORS: dict[str, Callable[[int], paths.GeneratedPath]] = {
    "walk": paths.random_walk,
    "hater": paths.hater,
}


def _marks(kind: str, seed: int) -> list[pytest.MarkDecorator]:
    reason = KNOWN_FAILURES.get((kind, seed))
    return [pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)] if reason else []


@pytest.mark.parametrize(
    ("build", "seed"),
    [
        pytest.param(build, seed, id=f"{kind}-{seed}", marks=_marks(kind, seed))
        for kind, build in GENERATORS.items()
        for seed in seeds(os.environ.get(SEEDS_ENV))
    ],
)
def test_every_rule_holds_after_every_bar_and_a_restart_changes_nothing(
    tmp_path: Path, build: Callable[[int], paths.GeneratedPath], seed: int
) -> None:
    """No pre-registration rule breaks on any bar, and restarting serve leaves the ledger as is.

    The oracle collects one line per broken rule (seed, bar, rule, evidence).
    The straight run, the same path with no restarts, must leave the same
    signals, trades, stops and portfolio marks.
    """
    path = build(seed)
    oracle = Oracle(path)
    restarted_db = tmp_path / "restarted.sqlite3"
    exits = run_path(path, restarted_db, after_bar=oracle.after_bar)
    oracle.finish(restarted_db, exits)
    straight_db = tmp_path / "straight.sqlite3"
    run_path(path, straight_db, restarts=False)
    assert (
        oracle.violations,
        differences(fingerprint(restarted_db), fingerprint(straight_db)),
    ) == (
        [],
        [],
    ), "\n".join(path.notes)


def test_after_a_restart_open_positions_keep_their_last_marks(tmp_path: Path) -> None:
    """Equity right after a boot values open positions at the last closes, not at cost.

    Random-walk seed 3 holds SOL across its restart. The tracker is rebuilt
    from the trades at fill prices, so unless the warm-up marks it, the BTC and
    ETH bars handled first after the boot store an equity of 4999.75 where the
    last closes give 5082.70, and the §5.5 exposure check and the RO-08
    drawdown read the same stale values (issue #863).
    """
    path = paths.random_walk(3)
    oracle = Oracle(path)
    db = tmp_path / "ledger.sqlite3"
    run_path(path, db, after_bar=oracle.after_bar)
    assert [v for v in oracle.violations if f" {MARKS_AFTER_BOOT}: " in v] == []


def test_one_seed_builds_the_same_path_every_time() -> None:
    """A seed reproduces its whole path: bars, faults and restarts, so a failure replays."""
    first, again = paths.hater(3), paths.hater(3)
    assert (first.market, first.book_outages, first.late_bars, first.restarts) == (
        again.market,
        again.book_outages,
        again.late_bars,
        again.restarts,
    )


def test_seeds_come_from_the_environment_variable() -> None:
    """``N@START`` gives N seeds from START; no value gives the fixed PR seeds 0 to 9."""
    assert (list(seeds("3@20261007")), list(seeds("2")), seeds(None)) == (
        [20261007, 20261008, 20261009],
        [0, 1],
        range(10),
    )
