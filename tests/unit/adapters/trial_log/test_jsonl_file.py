"""Unit tests for the hash-chained JSON Lines trial log.

Invariants under test (openspec/archive/357-trial-log/change.md D-2, R-2):

- Appended trials read back equal and in order.
- Editing, removing, reordering or emptying lines breaks the chain, and both
  reading and appending then fail instead of returning a lower trial count.
- Concurrent appenders never fork the chain (issue #855).
- A repair drops only a torn last line, records the drop in the chain, keeps
  the trial count, and lets the next append through; a log broken anywhere
  else is refused (issue #891).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.trial_log.jsonl_file import GENESIS_SHA256, JsonlTrialLog
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.errors import TrialLogError
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.infrastructure.file_lock import FileLock

Edit = Callable[[list[str]], list[str]]


def _record(n: int, sharpe: float | None = 0.25) -> TrialRecord:
    return TrialRecord(
        recorded_at=datetime(2026, 10, 3, 14, n, tzinfo=UTC),
        strategy_id="swing-mvp1-phase1-btcusdt",
        parameters={"donchian_period": str(20 + n)},
        split=DataSplit.VALIDATION if n % 2 else DataSplit.TRAIN,
        window_start=datetime(2024, 1, 1, tzinfo=UTC),
        window_end=datetime(2024, 3, 31, tzinfo=UTC),
        datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
        metrics={"fills_count": str(n)},
        sharpe=sharpe,
        git_sha="d" * 40,
        git_dirty=bool(n % 2),
    )


def _sharpe(i: int) -> float | None:
    return None if i == 2 else 0.1 * i


def _log_with(tmp_path: Path, n: int) -> JsonlTrialLog:
    log = JsonlTrialLog(tmp_path / "nested" / "trial-log.jsonl")
    for i in range(n):
        log.append(_record(i, sharpe=_sharpe(i)))
    return log


def _rewrite(log: JsonlTrialLog, edit: Edit) -> None:
    lines = log.path.read_text(encoding="utf-8").splitlines()
    log.path.write_text("".join(f"{line}\n" for line in edit(lines)), encoding="utf-8")


def _edit_middle(lines: list[str]) -> list[str]:
    return [lines[0], lines[1].replace('"fills_count": "1"', '"fills_count": "7"'), lines[2]]


def test_missing_file_reads_as_no_trials(tmp_path: Path) -> None:
    """Before the first run there are zero trials, not an error."""
    assert JsonlTrialLog(tmp_path / "t.jsonl").records() == ()


def test_appended_trials_read_back_in_order(tmp_path: Path) -> None:
    """Every field survives storage and the order is the append order."""
    log = _log_with(tmp_path, 3)
    assert log.records() == tuple(_record(i, _sharpe(i)) for i in range(3))


def test_first_line_chains_from_genesis(tmp_path: Path) -> None:
    """The chain is anchored, so dropping the first line is detectable too."""
    log = _log_with(tmp_path, 2)
    first, second = (json.loads(x) for x in log.path.read_text(encoding="utf-8").splitlines())
    assert first["prev_sha256"] == GENESIS_SHA256
    assert second["prev_sha256"] == first["sha256"]


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(_edit_middle, id="edited"),
        pytest.param(lambda ls: [ls[0], ls[2]], id="removed-middle"),
        pytest.param(lambda ls: ls[1:], id="removed-first"),
        pytest.param(lambda ls: [ls[1], ls[0], ls[2]], id="reordered"),
        pytest.param(lambda ls: [], id="emptied"),
        pytest.param(lambda ls: [*ls, "{oops"], id="not-json"),
    ],
)
def test_tampering_breaks_reads_and_appends(tmp_path: Path, edit: Edit) -> None:
    """Any change that could lower the trial count stops both reading and appending."""
    log = _log_with(tmp_path, 3)
    _rewrite(log, edit)
    with pytest.raises(TrialLogError):
        log.records()
    with pytest.raises(TrialLogError):
        log.append(_record(9))


def test_removing_the_last_line_is_not_detectable_by_the_chain(tmp_path: Path) -> None:
    """Known limit: a truncated tail is a valid shorter chain; git history guards it."""
    log = _log_with(tmp_path, 3)
    _rewrite(log, lambda ls: ls[:2])
    assert len(log.records()) == 2


def test_malformed_record_fields_are_reported_with_the_line(tmp_path: Path) -> None:
    """A chained line whose record fails validation still fails loudly."""
    log = _log_with(tmp_path, 1)
    raw = json.loads(log.path.read_text(encoding="utf-8"))
    body = {k: v for k, v in raw.items() if k not in {"prev_sha256", "sha256"}}
    body["split"] = "oos"
    rebuilt = JsonlTrialLog(tmp_path / "rebuilt.jsonl")
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((canonical + GENESIS_SHA256).encode("utf-8")).hexdigest()
    rebuilt.path.write_text(
        json.dumps({**body, "prev_sha256": GENESIS_SHA256, "sha256": digest}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TrialLogError, match="line 1"):
        rebuilt.records()


# -- concurrent appenders (issue #855) ----------------------------------------


def test_an_append_waits_while_another_appender_holds_the_lock(tmp_path: Path) -> None:
    """An append writes nothing while another appender holds the lock, then links to its line.

    Without the lock both appenders read the same last line and write two
    lines with the same ``prev_sha256``: a forked chain that no read accepts.
    """
    log = _log_with(tmp_path, 1)
    other = FileLock(log.lock_path)
    assert other.try_acquire()
    waiting = threading.Thread(target=log.append, args=(_record(1),))
    waiting.start()
    waiting.join(timeout=0.3)
    lines_while_held = len(log.path.read_text(encoding="utf-8").splitlines())
    other.release()
    waiting.join(timeout=10)
    assert (lines_while_held, len(log.records())) == (1, 2)


def test_concurrent_appenders_leave_one_unbroken_chain(tmp_path: Path) -> None:
    """Four appenders racing 25 appends each leave 100 trials on one verified chain."""
    log = JsonlTrialLog(tmp_path / "trial-log.jsonl")

    def appender(worker: int) -> None:
        for i in range(25):
            log.append(_record(i, sharpe=float(worker)))

    workers = [threading.Thread(target=appender, args=(w,)) for w in range(4)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    expected = sorted((float(w), str(20 + i)) for w in range(4) for i in range(25))
    assert sorted((r.sharpe, r.parameters["donchian_period"]) for r in log.records()) == expected


_REPAIRED_AT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _tear(log: JsonlTrialLog) -> bytes:
    """Cut a third append halfway, as a power loss would, and return the bytes left."""
    whole = json.dumps({"recorded_at": "2026-10-03T14:02:00+00:00", "sharpe": 0.2})
    torn = whole[: len(whole) // 2].encode()
    with log.path.open("ab") as fh:
        fh.write(torn)
    return torn


def test_a_torn_last_line_blocks_the_next_append(tmp_path: Path) -> None:
    """Without a repair the next run cannot record its trial, and the error names the fix."""
    log = _log_with(tmp_path, 2)
    _tear(log)
    with pytest.raises(TrialLogError, match=r"line 3 is malformed.*hermes trials repair"):
        log.append(_record(5))


def test_a_repair_drops_the_torn_line_and_keeps_every_trial(tmp_path: Path) -> None:
    """The two whole trials survive; the torn bytes are gone and named by their hash."""
    log = _log_with(tmp_path, 2)
    torn = _tear(log)
    dropped = log.repair(repaired_at=_REPAIRED_AT)
    assert dropped is not None
    assert (dropped.number, dropped.size, dropped.sha256, log.records()) == (
        3,
        len(torn),
        hashlib.sha256(torn).hexdigest(),
        (_record(0, sharpe=_sharpe(0)), _record(1, sharpe=_sharpe(1))),
    )


def test_a_repair_records_the_drop_as_a_chain_line(tmp_path: Path) -> None:
    """The last line names the drop and links to the last whole trial."""
    log = _log_with(tmp_path, 2)
    torn = _tear(log)
    log.repair(repaired_at=_REPAIRED_AT)
    lines = [json.loads(raw) for raw in log.path.read_text(encoding="utf-8").splitlines()]
    last = {k: v for k, v in lines[-1].items() if k != "sha256"}
    assert (len(lines), last) == (
        3,
        {
            "kind": "repair",
            "repaired_at": "2026-10-17T12:00:00+00:00",
            "dropped_line": 3,
            "dropped_bytes": len(torn),
            "dropped_sha256": hashlib.sha256(torn).hexdigest(),
            "prev_sha256": lines[1]["sha256"],
        },
    )


def test_after_a_repair_the_next_append_lands_and_counts(tmp_path: Path) -> None:
    """The next run records its trial after the repair line, and the count goes up by one."""
    log = _log_with(tmp_path, 2)
    _tear(log)
    log.repair(repaired_at=_REPAIRED_AT)
    log.append(_record(5))
    assert [r.parameters["donchian_period"] for r in log.records()] == ["20", "21", "25"]


def test_a_torn_first_line_repairs_to_a_log_with_no_trials(tmp_path: Path) -> None:
    """A cut first append leaves only the repair line, linked to the genesis hash."""
    log = JsonlTrialLog(tmp_path / "trial-log.jsonl")
    _tear(log)
    log.repair(repaired_at=_REPAIRED_AT)
    line = json.loads(log.path.read_text(encoding="utf-8"))
    assert (line["prev_sha256"], log.records()) == (GENESIS_SHA256, ())


def test_a_repair_with_nothing_torn_changes_nothing(tmp_path: Path) -> None:
    """A whole log is left byte for byte as it was."""
    log = _log_with(tmp_path, 2)
    before = log.path.read_bytes()
    assert (log.repair(repaired_at=_REPAIRED_AT), log.path.read_bytes()) == (None, before)


def test_a_repair_refuses_a_log_edited_before_the_torn_line(tmp_path: Path) -> None:
    """An edit breaks the chain in the middle; a repair never hides it."""
    log = _log_with(tmp_path, 2)
    _rewrite(log, lambda lines: [lines[0].replace('"20"', '"99"'), *lines[1:]])
    _tear(log)
    before = log.path.read_bytes()
    with pytest.raises(TrialLogError, match="hash chain broken at line 1"):
        log.repair(repaired_at=_REPAIRED_AT)
    assert log.path.read_bytes() == before
