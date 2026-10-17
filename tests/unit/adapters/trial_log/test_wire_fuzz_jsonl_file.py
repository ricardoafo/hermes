"""Fuzz property for the trial log reader (issue #842).

For any file content, ``JsonlTrialLog.records`` returns trials or raises
``TrialLogError``, nothing else. The hash chain refuses an edited line, so to
reach the record parser each mutated body is re-chained with a valid hash:
the file a bug in our own writer, or a careful hand edit, could leave behind.
The seed is the body ``JsonlTrialLog.append`` writes for a synthetic trial.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from hypothesis import example, given
from hypothesis import strategies as st

from hermes.adapters.trial_log.jsonl_file import (
    GENESIS_SHA256,
    JsonlTrialLog,
    _chain_hash,
    _serialise,
)
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.errors import TrialLogError
from hermes.domain.market_data.dataset_manifest import DataSplit
from tests.unit.adapters.wire_fuzz import FUZZ_SLOW, foreign_error, mutated

_TRIAL: Final = TrialRecord(
    recorded_at=datetime(2026, 10, 3, 14, 0, tzinfo=UTC),
    strategy_id="swing-fuzz",
    parameters={"donchian_period": "20"},
    split=DataSplit.TRAIN,
    window_start=datetime(2024, 1, 1, tzinfo=UTC),
    window_end=datetime(2024, 3, 31, tzinfo=UTC),
    datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
    metrics={"fills_count": "3"},
    sharpe=0.25,
    git_sha="d" * 40,
    git_dirty=False,
)


_SEED: Final = _serialise(_TRIAL)
"""The body ``JsonlTrialLog.append`` writes for the synthetic trial."""


def _chained(body: object) -> str:
    if not isinstance(body, dict):
        return json.dumps(body) + "\n"
    line = {**body, "prev_sha256": GENESIS_SHA256, "sha256": _chain_hash(body, GENESIS_SHA256)}
    return json.dumps(line, sort_keys=True) + "\n"


@FUZZ_SLOW
@given(data=st.data())
@example(data=None)
def test_a_rechained_trial_line_reads_or_raises_trial_log_error(
    data: st.DataObject | None, tmp_path: Path
) -> None:
    """Invariant: any validly chained trial line reads as a trial or raises TrialLogError."""
    seed = _SEED
    body = (
        {**seed, "parameters": ["not", "a", "mapping"]}
        if data is None
        else data.draw(mutated(seed))
    )
    log = JsonlTrialLog(tmp_path / "trial-log.jsonl")
    log.path.write_text(_chained(body), encoding="utf-8")
    assert foreign_error(log.records, TrialLogError) == ""


@FUZZ_SLOW
@given(raw=st.one_of(st.binary(max_size=24), st.just(b"\xff\xfe\n"), st.just(b"[" * 5000)))
@example(raw=b"\xff\xfe\n")
@example(raw=b"[" * 5000)
def test_a_trial_log_that_is_not_json_lines_raises_trial_log_error(
    raw: bytes, tmp_path: Path
) -> None:
    """Invariant: bytes that are not UTF-8 JSON lines make records() raise TrialLogError."""
    log = JsonlTrialLog(tmp_path / "trial-log.jsonl")
    log.path.write_bytes(raw)
    assert foreign_error(log.records, TrialLogError) == ""
