"""A process killed mid-append leaves a torn line; a confirmed repair recovers (issue #891).

Each test runs the real append in a child process whose file write puts out
half the line and then dies with ``os._exit``, as a power loss or SIGKILL
would: no exception handler, no flush of the rest. The parent then makes the
next append, sees it refused, runs the repair command with its typed
confirmation, and appends again.
"""

import io
import subprocess
import sys
import textwrap
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from hermes.adapters.journal.jsonl_store import JsonlDecisionJournal
from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.errors import TrialLogError
from hermes.domain.journal.entries import Decision, OutcomeKind
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.entrypoints import journal as journal_cli
from hermes.entrypoints import trials as trials_cli
from hermes.ports.decision_journal import JournalIntegrityError

pytestmark = pytest.mark.integration

_KILLED_EXIT = 9
_REPO_ROOT = Path(__file__).resolve().parents[3]

_DIE_HALFWAY = """
import os, pathlib
real_open = pathlib.Path.open

class _HalfThenDie:
    def __init__(self, handle):
        self._handle = handle
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)
    def write(self, text):
        self._handle.write(text[: len(text) // 2])
        self._handle.flush()
        os._exit(9)

def open_then_die_on_append(self, mode="r", *args, **kwargs):
    handle = real_open(self, mode, *args, **kwargs)
    return _HalfThenDie(handle) if mode.startswith("a") else handle

pathlib.Path.open = open_then_die_on_append
"""


def _trial(n: int) -> TrialRecord:
    return TrialRecord(
        recorded_at=datetime(2026, 10, 17, 12, n, tzinfo=UTC),
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


def _decision(entry_id: str) -> Decision:
    return Decision(
        entry_id=entry_id,
        recorded_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        decision="Repair a torn journal",
        options=("by hand", "hermes journal repair"),
        chosen="hermes journal repair",
        reasoning="keeps a record of the drop",
        expected_outcome="appends work again",
        review_on=date(2026, 11, 1),
        outcome_kind=OutcomeKind.PROCESS,
        corrects=None,
    )


def _killed_mid_append(body: str) -> int:
    """Run ``body`` after the half-write patch in a child; return its exit code."""
    script = _DIE_HALFWAY + textwrap.dedent(body)
    return subprocess.run(  # noqa: S603 - fixed interpreter, script built here
        [sys.executable, "-c", script], check=False, cwd=_REPO_ROOT
    ).returncode


def test_a_trial_append_killed_halfway_is_repaired_and_the_next_run_records(
    tmp_path: Path,
) -> None:
    """Killed append, refused next run, confirmed repair, then the next trial lands."""
    log = JsonlTrialLog(tmp_path / "trial-log.jsonl")
    log.append(_trial(0))
    code = _killed_mid_append(
        f"""
        from tests.integration.scenarios.test_chaos_torn_append import _trial
        from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
        from pathlib import Path
        JsonlTrialLog(Path({str(log.path)!r})).append(_trial(1))
        """
    )
    with pytest.raises(TrialLogError, match="line 2 is malformed"):
        log.append(_trial(2))
    repaired = trials_cli.main(
        ["repair", "--trial-log", str(log.path), "--confirm", "REPAIR"],
        stream=io.StringIO(),
        err=io.StringIO(),
    )
    log.append(_trial(2))
    periods = [r.parameters["donchian_period"] for r in log.records()]
    assert (code, repaired, periods) == (_KILLED_EXIT, 0, ["20", "22"])


def test_a_journal_append_killed_halfway_is_repaired_and_the_next_decision_records(
    tmp_path: Path,
) -> None:
    """Killed append, refused next decision, confirmed repair, then the next one lands."""
    path = tmp_path / "decision-journal.jsonl"
    journal = JsonlDecisionJournal(path)
    journal.append(_decision("d1"))
    code = _killed_mid_append(
        f"""
        from tests.integration.scenarios.test_chaos_torn_append import _decision
        from hermes.adapters.journal.jsonl_store import JsonlDecisionJournal
        from pathlib import Path
        JsonlDecisionJournal(Path({str(path)!r})).append(_decision("d2"))
        """
    )
    with pytest.raises(JournalIntegrityError, match=":2: malformed"):
        journal.append(_decision("d3"))
    repaired = journal_cli.main(
        [
            "--journal",
            str(path),
            "--store-dir",
            str(tmp_path / "cert"),
            "repair",
            "--confirm",
            "REPAIR",
        ],
        stream=io.StringIO(),
        err=io.StringIO(),
    )
    journal.append(_decision("d3"))
    ids = [e.entry_id for e in journal.entries()]
    assert (code, repaired, ids) == (_KILLED_EXIT, 0, ["d1", "d3"])
