"""Unit tests for scripts/backtest/rechain_trials.py (issue #1294).

Invariants under test:

- Joining a fork keeps every trial exactly once: the output reads back as the
  base's trials followed by the fork's new trials, in order, on one verified chain.
- A re-chained trial's body is byte for byte the fork's; only the chain fields change.
- Logs that are not a fork with a common start are refused and leave no output.
- The command prints counts only, never a field value (ADR-023 §7).
"""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.market_data.dataset_manifest import DataSplit
from tests.support.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "scripts" / "backtest" / "rechain_trials.py"
_MARKER_SHARPE = 0.123456789


def _script() -> ModuleType:
    """Import the script module, which is not part of a package."""
    spec = importlib.util.spec_from_file_location("rechain_trials_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(strategy_id: str, n: int) -> TrialRecord:
    return TrialRecord(
        recorded_at=datetime(2026, 10, 19, 10, n, tzinfo=UTC),
        strategy_id=strategy_id,
        parameters={"candidate": strategy_id},
        split=DataSplit.TRAIN,
        window_start=datetime(2020, 9, 1, tzinfo=UTC),
        window_end=datetime(2024, 6, 30, tzinfo=UTC),
        datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
        metrics={"fills_count": str(n)},
        sharpe=_MARKER_SHARPE,
        git_sha="d" * 40,
        git_dirty=False,
    )


def _log(path: Path, records: list[TrialRecord]) -> Path:
    log = JsonlTrialLog(path)
    for record in records:
        log.append(record)
    return path


def _fork(
    tmp_path: Path, shared: int, ours: int, theirs: int
) -> tuple[Path, Path, list[TrialRecord]]:
    """Write a base and a fork that share ``shared`` trials, then add ``ours`` and ``theirs``.

    Returns the two paths and the trial order the joined log must hold.
    """
    start = [_record("main-c0", i) for i in range(shared)]
    base_new = [_record("batch-a-c1", i) for i in range(ours)]
    fork_new = [_record("batch-b-c1", i) for i in range(theirs)]
    base = _log(tmp_path / "base.jsonl", start + base_new)
    fork = _log(tmp_path / "fork.jsonl", start + fork_new)
    return base, fork, start + base_new + fork_new


def test_join_holds_every_trial_once_in_order(tmp_path: Path) -> None:
    """The joined log reads back as base trials then the fork's new trials, chain verified."""
    base, fork, expected = _fork(tmp_path, shared=3, ours=2, theirs=4)
    out = tmp_path / "out.jsonl"

    _script().rechain(base, fork, out)

    assert JsonlTrialLog(out).records() == tuple(expected)


def test_rechained_bodies_match_the_fork(tmp_path: Path) -> None:
    """Each re-chained line equals the fork's line once the two chain fields are removed."""
    base, fork, _ = _fork(tmp_path, shared=1, ours=1, theirs=3)
    out = tmp_path / "out.jsonl"

    _script().rechain(base, fork, out)

    def bodies(lines: list[str]) -> list[dict[str, object]]:
        return [
            {k: v for k, v in json.loads(raw).items() if k not in {"prev_sha256", "sha256"}}
            for raw in lines
        ]

    fork_tail = fork.read_text(encoding="utf-8").splitlines()[1:]
    out_tail = out.read_text(encoding="utf-8").splitlines()[2:]
    assert bodies(out_tail) == bodies(fork_tail)


def test_counts_name_each_part(tmp_path: Path) -> None:
    """The returned counts are the base, fork, shared, re-chained and output line counts."""
    base, fork, _ = _fork(tmp_path, shared=2, ours=3, theirs=4)

    counts = _script().rechain(base, fork, tmp_path / "out.jsonl")

    assert counts == {"base": 5, "fork": 6, "shared": 2, "rechained": 4, "out": 9}


def test_no_common_start_is_refused(tmp_path: Path) -> None:
    """Two logs with different first lines are not a fork; nothing is written."""
    base = _log(tmp_path / "base.jsonl", [_record("batch-a-c1", 0)])
    fork = _log(tmp_path / "fork.jsonl", [_record("batch-b-c1", 0)])
    out = tmp_path / "out.jsonl"
    script = _script()

    with pytest.raises(script.RefusedError, match="shares no first line"):
        script.rechain(base, fork, out)
    assert not out.exists()


def test_fork_that_already_continues_base_is_refused(tmp_path: Path) -> None:
    """A fork whose start is the whole base needs no re-chain; it is refused, not duplicated."""
    base, fork, _ = _fork(tmp_path, shared=2, ours=0, theirs=2)
    script = _script()

    with pytest.raises(script.RefusedError, match="already continues"):
        script.rechain(base, fork, tmp_path / "out.jsonl")


def test_broken_chain_exits_3_without_output(tmp_path: Path) -> None:
    """An edited line in the fork breaks its chain: exit 3 and no output file."""
    base, fork, _ = _fork(tmp_path, shared=1, ours=1, theirs=2)
    lines = fork.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1].replace('"fills_count": "1"', '"fills_count": "9"')
    fork.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    out = tmp_path / "out.jsonl"

    code = _script().main(["--base", str(base), "--fork", str(fork), "--out", str(out)])

    assert (code, out.exists()) == (3, False)


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    """An existing ``--out`` file exits 3 and keeps its content."""
    base, fork, _ = _fork(tmp_path, shared=1, ours=1, theirs=1)
    out = tmp_path / "out.jsonl"
    out.write_text("keep\n", encoding="utf-8")

    code = _script().main(["--base", str(base), "--fork", str(fork), "--out", str(out)])

    assert (code, out.read_text(encoding="utf-8")) == (3, "keep\n")


def test_output_prints_counts_and_no_field_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The success line holds counts only: no strategy id, metric or sharpe value."""
    base, fork, _ = _fork(tmp_path, shared=1, ours=1, theirs=2)

    code = _script().main(
        ["--base", str(base), "--fork", str(fork), "--out", str(tmp_path / "out.jsonl")]
    )

    printed = capsys.readouterr()
    assert code == 0
    assert printed.out == (
        "base 2 lines, fork 3 lines, shared 1, re-chained 2, "
        "out 4 lines; chain verified, bodies identical\n"
    )
    assert str(_MARKER_SHARPE) not in printed.out + printed.err


@settings(max_examples=40, deadline=None)
@given(
    shared=st.integers(min_value=1, max_value=4),
    ours=st.integers(min_value=1, max_value=4),
    theirs=st.integers(min_value=1, max_value=4),
)
def test_any_fork_joins_without_loss(
    tmp_path_factory: pytest.TempPathFactory, shared: int, ours: int, theirs: int
) -> None:
    """For any shared start and two tails, the join holds shared + ours + theirs trials in order."""
    directory = tmp_path_factory.mktemp("fork")
    base, fork, expected = _fork(directory, shared=shared, ours=ours, theirs=theirs)
    out = directory / "out.jsonl"

    _script().rechain(base, fork, out)

    assert JsonlTrialLog(out).records() == tuple(expected)
