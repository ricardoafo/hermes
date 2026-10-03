"""Unit tests for the hash-chained JSON Lines trial log.

Invariants under test (openspec/changes/357-trial-log/change.md D-2, R-2):

- Appended trials read back equal and in order.
- Editing, removing, reordering or emptying lines breaks the chain, and both
  reading and appending then fail instead of returning a lower trial count.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.trial_log.jsonl_file import GENESIS_SHA256, JsonlTrialLog
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.errors import TrialLogError
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.ports.trial_log import TrialLogPort

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


def test_adapter_satisfies_the_port(tmp_path: Path) -> None:
    """The JSONL adapter can be injected wherever TrialLogPort is expected."""
    assert isinstance(JsonlTrialLog(tmp_path / "t.jsonl"), TrialLogPort)


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
