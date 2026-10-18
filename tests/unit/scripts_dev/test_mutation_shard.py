"""Unit tests for scripts/dev/mutation-shard.py (#1213).

Invariants under test:

- Every mutant lands in exactly one shard, whatever the shard count.
- The split does not depend on database row order, so legs that each run
  their own ``cosmic-ray init`` agree on it.
- Shards differ in size by at most one mutant.
- The verdict passes at exactly the floor and fails just below it.
- A mutant no shard ran makes the verdict FAIL, even at a 100% kill rate.
- Sessions are merged by what a mutant mutates, not by job id.
"""

import importlib.util
import random
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest
from cosmic_ray import work_item as cr_item
from cosmic_ray.work_db import WorkDB, use_db
from cosmic_ray.work_item import MutationSpec, WorkerOutcome, WorkItem, WorkResult
from hypothesis import given, settings
from hypothesis import strategies as st

_ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "mutation_shard", _ROOT / "scripts" / "dev" / "mutation-shard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["mutation_shard"] = module
    spec.loader.exec_module(module)
    return module


shard = _load()

_KILLED = WorkResult(worker_outcome=WorkerOutcome.NORMAL, test_outcome=cr_item.TestOutcome.KILLED)
_SURVIVED = WorkResult(
    worker_outcome=WorkerOutcome.NORMAL, test_outcome=cr_item.TestOutcome.SURVIVED
)
_SKIPPED = WorkResult(worker_outcome=WorkerOutcome.SKIPPED)


def _spec(row: int, module: str = "src/hermes/domain/lots/lot.py") -> MutationSpec:
    return MutationSpec(
        module_path=Path(module),
        operator_name="core/NumberReplacer",
        occurrence=0,
        start_pos=(row, 4),
        end_pos=(row, 5),
    )


def _session(
    path: Path, rows: Sequence[int], *, prefix: str, module: str = "src/hermes/domain/lots/lot.py"
) -> Path:
    """Write a session with one mutant per row, job ids starting with ``prefix``."""
    with use_db(str(path), WorkDB.Mode.create) as db:
        db.add_work_items(WorkItem.single(f"{prefix}{row}", _spec(row, module)) for row in rows)
    return path


def _key(row: int, module: str = "src/hermes/domain/lots/lot.py") -> tuple[object, ...]:
    return (module, "core/NumberReplacer", 0, row, 4, row, 5)


_keys = st.lists(
    st.tuples(
        st.sampled_from(("a.py", "b.py", "c.py")),
        st.sampled_from(("op/A", "op/B")),
        st.integers(0, 3),
        st.integers(1, 400),
        st.integers(0, 80),
        st.integers(1, 400),
        st.integers(0, 80),
    ),
    unique=True,
    max_size=60,
)


@settings(max_examples=100)
@given(_keys, st.integers(1, 9))
def test_every_mutant_lands_in_exactly_one_shard(
    keys: list[tuple[object, ...]], shards: int
) -> None:
    """The shards partition the mutants: none lost, none run twice."""
    assignment = shard.shard_of(keys, shards)
    expected_keys = set(keys)
    assert set(assignment) == expected_keys
    assert {index for index in assignment.values()} <= set(range(shards))


@settings(max_examples=100)
@given(_keys, st.integers(1, 9), st.randoms(use_true_random=False))
def test_the_split_does_not_depend_on_row_order(
    keys: list[tuple[object, ...]], shards: int, rng: random.Random
) -> None:
    """Two legs whose databases list the mutants in different orders agree on the split."""
    shuffled = list(keys)
    rng.shuffle(shuffled)
    expected = shard.shard_of(keys, shards)
    assert shard.shard_of(shuffled, shards) == expected


def test_shards_differ_in_size_by_at_most_one() -> None:
    """Ten mutants over four shards deal 3, 3, 2, 2."""
    assignment = shard.shard_of([_key(row) for row in range(1, 11)], 4)
    sizes = [list(assignment.values()).count(index) for index in range(4)]
    assert sizes == [3, 3, 2, 2]


@pytest.mark.parametrize("shards", [0, -1])
def test_fewer_than_one_shard_is_refused(shards: int) -> None:
    """Zero shards would silently run nothing."""
    with pytest.raises(ValueError, match="at least 1"):
        shard.shard_of([_key(1)], shards)


def test_select_keeps_its_share_and_skips_the_rest(tmp_path: Path) -> None:
    """Across three legs, each mutant is pending in exactly one session."""
    pending: list[str] = []
    for index in range(3):
        session = _session(tmp_path / f"s{index}.sqlite", range(1, 8), prefix=f"leg{index}-")
        assert shard.select(session, 3, index) == ((3, 7), (2, 7), (2, 7))[index]
        with use_db(str(session), WorkDB.Mode.open) as db:
            pending += [item.mutations[0].start_pos[0] for item in db.pending_work_items]
    assert sorted(pending) == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize("index", [-1, 3])
def test_select_refuses_an_index_outside_the_shards(tmp_path: Path, index: int) -> None:
    """A leg number past the matrix would skip every mutant and look like a pass."""
    session = _session(tmp_path / "s.sqlite", [1], prefix="j")
    with pytest.raises(ValueError, match=r"index must be in \[0, 3\)"):
        shard.select(session, 3, index)


def _results(
    results: Sequence[WorkResult | None],
) -> list[tuple[tuple[object, ...], WorkResult | None]]:
    return [(_key(row), result) for row, result in enumerate(results, 1)]


def test_a_kill_rate_exactly_at_the_floor_passes() -> None:
    """4 killed of 5 is 80.00%, the floor itself."""
    verdict = shard.merge(_results([_KILLED] * 4 + [_SURVIVED]), 80.0)
    assert (verdict.kill_rate, verdict.passed) == (80.0, True)


def test_a_kill_rate_just_below_the_floor_fails() -> None:
    """The same 80.00% fails a floor of 80.01%."""
    verdict = shard.merge(_results([_KILLED] * 4 + [_SURVIVED]), 80.01)
    assert verdict.passed is False


def test_a_mutant_no_shard_ran_fails_even_at_full_kill() -> None:
    """A shard cut off by its time cap leaves mutants unrun; that is never a pass."""
    verdict = shard.merge(_results([_KILLED, _KILLED, None, _SKIPPED]), 80.0)
    assert (verdict.kill_rate, verdict.missing, verdict.passed) == (
        100.0,
        (_key(3), _key(4)),
        False,
    )


def test_no_mutant_run_at_all_fails() -> None:
    """Every shard dying before exec gives FAIL, not a vacuous pass."""
    verdict = shard.merge(_results([_SKIPPED, _SKIPPED]), 80.0)
    assert (verdict.ran, verdict.passed) == (0, False)


def test_a_session_with_no_mutants_fails_even_at_a_zero_floor() -> None:
    """A config that plants nothing (a typo in module-path) must not read as a pass."""
    verdict = shard.merge([], 0.0)
    assert (verdict.total, verdict.passed) == (0, False)


def test_sessions_merge_by_mutant_not_by_job_id(tmp_path: Path) -> None:
    """Each leg's init gives new job ids; the verdict still sees one run per mutant."""
    sessions = [
        _session(tmp_path / f"s{index}.sqlite", [1, 2, 3, 4, 5], prefix=f"leg{index}-")
        for index in range(2)
    ]
    for index, session in enumerate(sessions):
        shard.select(session, 2, index)
        with use_db(str(session), WorkDB.Mode.open) as db:
            for item in db.pending_work_items:
                row = item.mutations[0].start_pos[0]
                db.set_result(item.job_id, _SURVIVED if row == 2 else _KILLED)
    summary = tmp_path / "summary.md"

    code = shard.main(["verdict", *map(str, sessions), "--floor", "80", "--summary", str(summary)])

    assert code == 0
    assert summary.read_text(encoding="utf-8").splitlines()[:3] == [
        "## Mutation run: PASS",
        "",
        "Kill rate 80.00% over 5 of 5 mutants (floor 80%).",
    ]


def test_the_summary_names_the_unrun_mutants_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A leg that never ran leaves its share unrun; the CLI exits 1 and says how many."""
    sessions = [
        _session(tmp_path / f"s{index}.sqlite", [1, 2, 3, 4], prefix=f"leg{index}-")
        for index in range(2)
    ]
    shard.select(sessions[0], 2, 0)
    with use_db(str(sessions[0]), WorkDB.Mode.open) as db:
        for item in db.pending_work_items:
            db.set_result(item.job_id, _KILLED)
    shard.select(sessions[1], 2, 1)

    code = shard.main(["verdict", *map(str, sessions), "--floor", "80"])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert lines[:4] == [
        "## Mutation run: FAIL",
        "",
        "Kill rate 100.00% over 2 of 4 mutants (floor 80%).",
        "2 mutants were not run by any shard, so the run is incomplete.",
    ]


def test_select_with_a_bad_index_exits_two(tmp_path: Path) -> None:
    """A usage error is exit 2, not a silent empty shard."""
    session = _session(tmp_path / "s.sqlite", [1], prefix="j")
    assert shard.main(["select", str(session), "--shards", "2", "--index", "2"]) == 2
