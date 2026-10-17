"""Unit tests for ``hermes trials repair`` (issue #891).

Invariants under test:

- Without ``--confirm REPAIR`` a torn log is refused (exit 1), named, and left
  byte for byte as it was.
- With it the torn line is dropped, the trials are kept, and the next backtest
  append works again.
- A whole log exits 0 and is not touched; a log broken before its last line
  exits 3 and is not touched.
"""

import io
import json
from datetime import UTC, datetime
from pathlib import Path

from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.entrypoints import trials

_TORN = b'{"recorded_at": "2026-10-03T14:0'


def _record(n: int) -> TrialRecord:
    return TrialRecord(
        recorded_at=datetime(2026, 10, 3, 14, n, tzinfo=UTC),
        strategy_id="swing-donchian-4h",
        parameters={"donchian_period": str(20 + n)},
        split=DataSplit.TRAIN,
        window_start=datetime(2020, 9, 1, tzinfo=UTC),
        window_end=datetime(2024, 6, 30, tzinfo=UTC),
        datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
        metrics={"fills_count": str(n)},
        sharpe=0.1,
        git_sha="d" * 40,
        git_dirty=False,
    )


def _torn_log(tmp_path: Path) -> JsonlTrialLog:
    log = JsonlTrialLog(tmp_path / "trial-log.jsonl")
    log.append(_record(0))
    with log.path.open("ab") as fh:
        fh.write(_TORN)
    return log


def _run(log: JsonlTrialLog, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = trials.main(["repair", "--trial-log", str(log.path), *args], stream=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_without_confirm_a_torn_log_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    """Exit 1, the torn line named with its size, and not one byte changed."""
    log = _torn_log(tmp_path)
    before = log.path.read_bytes()
    code, out, err = _run(log)
    assert (code, out, err.startswith("REFUSED:"), f"line 2 ({len(_TORN)} bytes" in err) == (
        1,
        "",
        True,
        True,
    )
    assert log.path.read_bytes() == before


def test_with_confirm_the_torn_line_goes_and_the_next_append_works(tmp_path: Path) -> None:
    """Exit 0; the one trial is kept and the next backtest records its trial."""
    log = _torn_log(tmp_path)
    code, out, _ = _run(log, "--confirm", "REPAIR")
    log.append(_record(1))
    assert (code, out.startswith("REPAIRED: dropped line 2"), len(log.records())) == (0, True, 2)


def test_a_whole_log_is_not_touched(tmp_path: Path) -> None:
    """Nothing torn: exit 0, one line saying so, the file unchanged."""
    log = JsonlTrialLog(tmp_path / "trial-log.jsonl")
    log.append(_record(0))
    before = log.path.read_bytes()
    code, out, _ = _run(log, "--confirm", "REPAIR")
    assert (code, out, log.path.read_bytes()) == (
        0,
        f"Nothing to repair: the last line of {log.path} is whole.\n",
        before,
    )


def test_a_log_edited_before_its_torn_line_exits_3_untouched(tmp_path: Path) -> None:
    """An edit is not a torn write: exit 3 with the chain error, nothing changed."""
    log = _torn_log(tmp_path)
    first, rest = log.path.read_bytes().split(b"\n", 1)
    line = json.loads(first)
    line["sharpe"] = 9.9
    log.path.write_bytes(json.dumps(line, sort_keys=True).encode() + b"\n" + rest)
    before = log.path.read_bytes()
    code, _, err = _run(log, "--confirm", "REPAIR")
    assert (code, "hash chain broken at line 1" in err, log.path.read_bytes()) == (3, True, before)


def test_bad_arguments_exit_2(tmp_path: Path) -> None:
    """An unknown subcommand is a usage error."""
    err = io.StringIO()
    assert trials.main(["mend"], stream=io.StringIO(), err=err) == 2


def test_help_lists_the_exit_codes(tmp_path: Path) -> None:
    """``hermes trials --help`` prints usage and the exit codes, and exits 0."""
    out = io.StringIO()
    code = trials.main(["--help"], stream=out)
    assert (code, "Exit codes: 0 repaired or nothing to repair" in out.getvalue()) == (0, True)
