"""Tests for ``hermes funnel --batch`` (ADR-026 §3-§5; issue #1059; contract §4).

Each test writes the synthetic signed batch and its candidates' evidence under
``tmp_path`` (``tests/support/candidate_evidence.py``: Train is 2021,
Validation 2022), a two-line trial log, and runs the real gate.
"""

import io
import json
import re
import shutil
from pathlib import Path

import pytest

from hermes.application.backtest.fold_evidence import fold_evidence
from hermes.entrypoints import funnel as funnel_module
from hermes.entrypoints import funnel_batch
from tests.support.candidate_evidence import (
    START,
    TRAIN_DAYS,
    write_candidate,
    write_trial_log,
)
from tests.support.research_batch import BATCH_PATH

_ALPHA = "swing-alpha"
_BETA = "swing-beta"
_STDOUT_LINE = re.compile(r"^(# .*|(PASS|FAIL|NA) swing-[a-z]+ [a-z_]+|(CANDIDATES|CHAMPION) .*)$")
"""Every line stdout may carry: a header, a verdict per check, the closing line. No values."""


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from the clone's root, with a short trial log beside it."""
    monkeypatch.chdir(tmp_path)
    write_trial_log(tmp_path / "trial-log.jsonl", [0.05, 0.06])


def _argv(tmp_path: Path, stage: str, *extra: str) -> list[str]:
    return [
        "--batch",
        BATCH_PATH,
        "--stage",
        stage,
        "--evidence-dir",
        str(tmp_path / "evidence"),
        "--trial-log",
        str(tmp_path / "trial-log.jsonl"),
        "--out",
        str(tmp_path / "out"),
        *extra,
    ]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = funnel_module.main(argv, stream=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _batch_without_evidence(root: Path) -> None:
    """Write the batch, then remove the only candidate folder written with it."""
    write_candidate(root, strategy_id=_ALPHA)
    shutil.rmtree(root / "evidence" / _ALPHA)


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class TestTrain:
    """``--stage train``: who goes on to Validation."""

    def test_both_strong_candidates_go_on(self, tmp_path: Path) -> None:
        """Exit 0, CANDIDATES alpha beta, and the same list in validation-candidates.json."""
        for strategy_id in (_ALPHA, _BETA):
            write_candidate(tmp_path, strategy_id=strategy_id, days=TRAIN_DAYS)

        code, out, _ = _run(_argv(tmp_path, "train"))

        assert (code, out.splitlines()[-1], _json(tmp_path / "out/validation-candidates.json")) == (
            0,
            "CANDIDATES swing-alpha swing-beta",
            {"batch_id": "b1", "stage": "train", "candidates": [_ALPHA, _BETA]},
        )

    def test_stdout_carries_no_metric_value(self, tmp_path: Path) -> None:
        """ADR-023 §7: only headers, PASS/FAIL/NA <id> <check> and the closing line."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)

        _, out, _ = _run(_argv(tmp_path, "train"))

        assert [line for line in out.splitlines() if not _STDOUT_LINE.match(line)] == []

    def test_each_check_is_one_stdout_line(self, tmp_path: Path) -> None:
        """Alpha's validation_vs_train is NA on Train; beta, with no folder, fails one check."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)

        _, out, _ = _run(_argv(tmp_path, "train"))

        lines = out.splitlines()
        assert (
            "NA swing-alpha validation_vs_train" in lines,
            [line for line in lines if " swing-beta " in line],
        ) == (True, ["FAIL swing-beta missing_evidence"])

    def test_no_candidate_going_on_exits_one(self, tmp_path: Path) -> None:
        """No evidence at all: every candidate fails, CANDIDATES none, exit 1."""
        _batch_without_evidence(tmp_path)

        code, out, _ = _run(_argv(tmp_path, "train"))

        assert (code, out.splitlines()[-1]) == (1, "CANDIDATES none")

    def test_every_candidate_gets_a_verdict_file_and_the_details_file(self, tmp_path: Path) -> None:
        """verdicts/<id>.json for both, and funnel-batch.txt with the closing line last."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)

        _run(_argv(tmp_path, "train"))

        out = tmp_path / "out"
        verdicts = sorted(p.name for p in (out / "verdicts").iterdir())
        details = (out / "funnel-batch.txt").read_text(encoding="utf-8").splitlines()
        assert (verdicts, details[0], details[-1]) == (
            ["swing-alpha.json", "swing-beta.json"],
            "# batch b1 stage train",
            "CANDIDATES swing-alpha",
        )

    def test_the_details_file_lists_each_check_under_its_candidate(self, tmp_path: Path) -> None:
        """Beta, with no folder, gets its heading and one FAIL line naming the stage."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)

        _run(_argv(tmp_path, "train"))

        details = (tmp_path / "out/funnel-batch.txt").read_text(encoding="utf-8").splitlines()
        # status padded to 4, check to 24: "missing_evidence" is 16 characters, so 8 spaces.
        assert details[-3:] == [
            "## swing-beta",
            "FAIL missing_evidence" + " " * 8 + " no Train evidence",
            "CANDIDATES swing-alpha",
        ]

    def test_a_verdict_file_keeps_the_contract_key_order(self, tmp_path: Path) -> None:
        """Contract §5 order, two-space indent: the committed file diffs cleanly between runs."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)

        _run(_argv(tmp_path, "train"))

        text = (tmp_path / "out/verdicts/swing-beta.json").read_text(encoding="utf-8")
        assert text.splitlines()[:4] == [
            "{",
            '  "schema": "hermes.verdict/1",',
            '  "batch_id": "b1",',
            '  "strategy_id": "swing-beta",',
        ]

    def test_a_second_run_into_the_same_out_rewrites_it(self, tmp_path: Path) -> None:
        """A retried job writes over its own outputs: exit 0 again, same closing line."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)
        _run(_argv(tmp_path, "train"))

        code, out, _ = _run(_argv(tmp_path, "train"))

        assert (code, out.splitlines()[-1]) == (0, "CANDIDATES swing-alpha")


class TestValidation:
    """``--stage validation``: the champion by the signed rule."""

    def test_a_strong_pair_names_a_champion(self, tmp_path: Path) -> None:
        """Equal evidence, equal DSR: both survive and the tie goes to the lower id, alpha."""
        for strategy_id in (_ALPHA, _BETA):
            write_candidate(tmp_path, strategy_id=strategy_id)

        code, out, _ = _run(_argv(tmp_path, "validation"))

        assert (code, out.splitlines()[-1], _json(tmp_path / "out/selection.json")) == (
            0,
            "CHAMPION swing-alpha",
            {
                "batch_id": "b1",
                "stage": "validation",
                "survivors": [_ALPHA, _BETA],
                "champion": _ALPHA,
            },
        )

    def test_no_survivor_exits_one_with_champion_none(self, tmp_path: Path) -> None:
        """No evidence: no survivor, CHAMPION none, selection.json champion null, exit 1."""
        _batch_without_evidence(tmp_path)

        code, out, _ = _run(_argv(tmp_path, "validation"))

        selection = _json(tmp_path / "out/selection.json")
        assert (code, out.splitlines()[-1], selection) == (
            1,
            "CHAMPION none",
            {"batch_id": "b1", "stage": "validation", "survivors": [], "champion": None},
        )

    def test_train_evidence_at_validation_is_an_evidence_error(self, tmp_path: Path) -> None:
        """A Train-only run judged as Validation is refused: exit 3, nothing written."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)

        code, _, err = _run(_argv(tmp_path, "validation"))

        assert (
            code,
            err.startswith("ERROR: batch b1 validation:"),
            (tmp_path / "out").exists(),
        ) == (
            3,
            True,
            False,
        )


class TestRefusals:
    """Bad input never yields a verdict."""

    def test_a_draft_batch_is_refused(self, tmp_path: Path) -> None:
        """Only a SIGNED batch binds the thresholds: exit 1, REFUSED, nothing written."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)
        batch_file = tmp_path / BATCH_PATH
        batch_file.write_text(
            batch_file.read_text(encoding="utf-8").replace("status: SIGNED", "status: DRAFT"),
            encoding="utf-8",
        )

        code, out, _ = _run(_argv(tmp_path, "train"))

        assert (code, out.splitlines()[-1], (tmp_path / "out").exists()) == (
            1,
            "REFUSED: batch b1 is DRAFT; only a SIGNED batch is judged",
            False,
        )

    def test_a_missing_batch_file_is_exit_three(self, tmp_path: Path) -> None:
        """No batch document: a data error."""
        code, _, err = _run(_argv(tmp_path, "train"))

        assert (code, err.startswith("ERROR: batch:")) == (3, True)

    def test_a_missing_trial_log_is_exit_three(self, tmp_path: Path) -> None:
        """No log means no N to deflate by; judging with N = 0 would be wrong, so it stops."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)
        (tmp_path / "trial-log.jsonl").unlink()

        code, _, err = _run(_argv(tmp_path, "train"))

        assert (code, err) == (
            3,
            f"ERROR: trial log {tmp_path / 'trial-log.jsonl'} does not exist\n",
        )

    def test_a_broken_trial_log_is_exit_three(self, tmp_path: Path) -> None:
        """An edited line breaks the hash chain: a data error, not a verdict."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)
        log = tmp_path / "trial-log.jsonl"
        log.write_text(log.read_text(encoding="utf-8").replace('"40"', '"41"', 1), encoding="utf-8")

        code, _, err = _run(_argv(tmp_path, "train"))

        assert (code, err.startswith("ERROR: trial log:")) == (3, True)

    def test_unreadable_evidence_is_exit_three(self, tmp_path: Path) -> None:
        """A funnel evidence file that is not JSON stops the run."""
        files = write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS).files
        files.funnel_evidence.write_text("{", encoding="utf-8")

        code, _, err = _run(_argv(tmp_path, "train"))

        assert (code, err.startswith("ERROR: batch b1 train:")) == (3, True)

    def test_an_unwritable_out_is_exit_three(self, tmp_path: Path) -> None:
        """--out names a file, not a folder: an output error, not a verdict."""
        write_candidate(tmp_path, strategy_id=_ALPHA, days=TRAIN_DAYS)
        (tmp_path / "out").write_text("", encoding="utf-8")

        code, _, err = _run(_argv(tmp_path, "train"))

        assert (code, err.startswith("ERROR: output:")) == (3, True)

    @pytest.mark.parametrize("dropped", ["--stage", "--evidence-dir", "--trial-log", "--out"])
    def test_every_flag_is_required(self, tmp_path: Path, dropped: str) -> None:
        """Contract §4 names five flags; any one missing is a usage error."""
        argv = _argv(tmp_path, "train")
        at = argv.index(dropped)
        del argv[at : at + 2]

        code, _, err = _run(argv)

        assert (code, err.startswith(f"ERROR: --batch needs {dropped}.")) == (2, True)

    def test_an_unknown_stage_is_a_usage_error(self, tmp_path: Path) -> None:
        """Only train and validation exist; OOS is never judged here."""
        code, _, _ = _run(_argv(tmp_path, "oos"))

        assert code == 2

    def test_the_single_pre_registration_path_still_parses_its_own_flags(self) -> None:
        """Without --batch, hermes funnel keeps its v2 usage: no strategy id is exit 2."""
        code, _, err = _run(["--evidence", "x.json"])

        assert (code, err.startswith("ERROR: strategy_id, --pre-registration")) == (2, True)

    def test_the_batch_usage_is_in_the_help(self) -> None:
        """-h shows both forms."""
        code, out, _ = _run(["-h"])

        assert (code, funnel_batch.USAGE in out) == (0, True)


def _add_folds(root: Path, strategy_id: str) -> int:
    """Add the §8.3 folds of the candidate's base curve to its funnel evidence, as the driver does.

    Returns the number of purged CPCV folds written, so a caller can show the
    evidence is not empty.
    """
    path = root / "evidence" / strategy_id / "funnel-evidence.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["folds"] = fold_evidence(START, body["daily_equity"])
    path.write_text(json.dumps(body), encoding="utf-8")
    return len(body["folds"]["purged_cpcv"]["folds"])


def _judged_outputs(
    root: Path, stage: str, monkeypatch: pytest.MonkeyPatch, *, with_folds: bool
) -> tuple[int, str, dict[str, bytes]]:
    """Write alpha (strong) and beta (no calibration fill), judge them, and return what came out."""
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    write_trial_log(root / "trial-log.jsonl", [0.05, 0.06])
    days = TRAIN_DAYS if stage == "train" else 730
    write_candidate(root, strategy_id=_ALPHA, days=days)
    write_candidate(root, strategy_id=_BETA, days=days, fills=0)
    if with_folds:
        assert (_add_folds(root, _ALPHA), _add_folds(root, _BETA)) == (10, 10)
    code, out, _ = _run(_argv(root, stage))
    files = {
        str(p.relative_to(root / "out")): p.read_bytes()
        for p in sorted((root / "out").rglob("*"))
        if p.is_file()
    }
    return code, out, files


class TestFoldEvidenceIsReportedOnly:
    """Pre-registration §8.3 folds are evidence only (#1181): no verdict reads them."""

    @pytest.mark.parametrize("stage", ["train", "validation"])
    def test_verdicts_are_identical_with_and_without_folds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
    ) -> None:
        """Same exit code, stdout, verdict records, summary and details file, byte for byte."""
        plain = _judged_outputs(tmp_path / "plain", stage, monkeypatch, with_folds=False)
        folded = _judged_outputs(tmp_path / "folds", stage, monkeypatch, with_folds=True)

        assert folded == plain

    def test_the_identical_verdicts_include_a_pass_and_a_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The comparison above is not vacuous: with folds, alpha is champion and beta fails."""
        code, out, _ = _judged_outputs(tmp_path, "validation", monkeypatch, with_folds=True)

        lines = out.splitlines()
        assert (code, "FAIL swing-beta calibration_fills" in lines, lines[-1]) == (
            0,
            True,
            "CHAMPION swing-alpha",
        )
