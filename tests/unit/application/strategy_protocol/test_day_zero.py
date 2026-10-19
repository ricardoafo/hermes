"""Unit tests for the shadow day-0 Ready checks and record (ADR-021 §2, #797).

Invariants (797-shadow-day-zero change.md R-1, R-2):

- Rule 2 passes only when the certification record is past UNTRUSTED and
  carries the fingerprint of the parameters about to run.
- Rule 1 passes only for a signed pre-registration whose hash the lab
  notebook holds.
- Rule 7 passes only when the strategy is the single enabled roster entry.
- Manual rules never block, and never count as passed.
- The tree hash depends on file paths and bytes, not on listing order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.files import LocalFiles
from hermes.application.strategy_protocol.day_zero import (
    MANUAL_RULES,
    Check,
    DayZeroRecord,
    Verdict,
    certification_check,
    notebook_check,
    ready,
    roster_check,
    sha256_tree,
)
from hermes.domain.calibration.certification import CertificationRecord, CertificationState
from tests.support.paths import REPO_ROOT

_FILES = LocalFiles()
_FP = "fp-1"
_AT = datetime(2026, 10, 15, tzinfo=UTC)


class TestCertificationCheck:
    """Rule 2: the backtest gate recorded for these parameters."""

    def test_certified_with_same_parameters_passes(self) -> None:
        """BACKTEST_CERTIFIED with the running fingerprint passes."""
        record = CertificationRecord(
            "s", state=CertificationState.BACKTEST_CERTIFIED, parameter_fingerprint=_FP
        )
        assert certification_check(record, _FP).verdict is Verdict.PASS

    @pytest.mark.parametrize(
        "record",
        [
            None,
            CertificationRecord("s"),
            CertificationRecord(
                "s", state=CertificationState.BACKTEST_CERTIFIED, parameter_fingerprint="other"
            ),
        ],
        ids=["no-record", "untrusted", "other-parameters"],
    )
    def test_missing_untrusted_or_changed_parameters_fail(
        self, record: CertificationRecord | None
    ) -> None:
        """No record, UNTRUSTED, or a gate passed with other parameters all fail."""
        assert certification_check(record, _FP).verdict is Verdict.FAIL


class TestNotebookCheck:
    """Rule 1: signed, and its hash in the lab notebook."""

    @staticmethod
    def _files(tmp_path: Path, *, status: str, in_notebook: bool) -> tuple[Path, Path]:
        pre_reg = tmp_path / "pre-registration-v1.md"
        pre_reg.write_text(f"---\nstatus: {status}\ncontent_sha256: abc123\n---\n# Body\n")
        notebook = tmp_path / "lab-notebook.md"
        notebook.write_text("hash abc123\n" if in_notebook else "no hash\n")
        return pre_reg, notebook

    def test_signed_and_logged_passes(self, tmp_path: Path) -> None:
        """A SIGNED status and the hash in the notebook pass."""
        assert notebook_check(
            *self._files(tmp_path, status="SIGNED", in_notebook=True), files=_FILES
        ).verdict is (Verdict.PASS)

    @pytest.mark.parametrize(
        ("status", "in_notebook"), [("DRAFT", True), ("SIGNED", False)], ids=["draft", "unlogged"]
    )
    def test_unsigned_or_unlogged_fails(
        self, tmp_path: Path, status: str, in_notebook: bool
    ) -> None:
        """A draft, or a hash the notebook lacks, fails."""
        files = self._files(tmp_path, status=status, in_notebook=in_notebook)
        assert notebook_check(*files, files=_FILES).verdict is Verdict.FAIL

    def test_repository_pre_registration_passes(self) -> None:
        """The signed v1 document's hash is in the v1 lab notebook."""
        root = REPO_ROOT / "docs" / "experiments" / "swing-mvp1"
        check = notebook_check(
            root / "pre-registration-v1.md", root / "lab-notebook.md", files=_FILES
        )
        assert check.verdict is Verdict.PASS


class TestRosterCheck:
    """Rule 7: one enabled strategy, the one starting."""

    def test_single_matching_entry_passes(self) -> None:
        """Only swing-donchian-4h enabled: pass."""
        assert roster_check(["swing-donchian-4h"], "swing-donchian-4h").verdict is Verdict.PASS

    @pytest.mark.parametrize("enabled", [[], ["other"], ["swing-donchian-4h", "other"]])
    def test_other_rosters_fail(self, enabled: list[str]) -> None:
        """No entry, another entry, or two entries fail."""
        assert roster_check(enabled, "swing-donchian-4h").verdict is Verdict.FAIL


class TestReady:
    """Manual rules wait for the Code Owner; a failed rule blocks."""

    def test_manual_rules_do_not_block(self) -> None:
        """Passes plus manual rules are ready."""
        checks = (Check(1, "a", Verdict.PASS, ""), *MANUAL_RULES)
        assert ready(checks) is True

    def test_one_failure_blocks(self) -> None:
        """Any failed rule is not ready."""
        assert ready((*MANUAL_RULES, Check(2, "b", Verdict.FAIL, ""))) is False

    def test_manual_rules_are_three_four_and_six(self) -> None:
        """ADR-021 §2 rules 3, 4 and 6 have no program check here."""
        assert [c.rule for c in MANUAL_RULES] == [3, 4, 6]


class TestTreeHash:
    """The config hash covers paths and bytes, nothing else."""

    def test_content_change_changes_hash(self, tmp_path: Path) -> None:
        """Editing one byte changes the hash; rewriting the same bytes does not."""
        (tmp_path / "a.yaml").write_text("x: 1\n")
        before = sha256_tree(tmp_path, files=_FILES)
        (tmp_path / "a.yaml").write_text("x: 1\n")
        same = sha256_tree(tmp_path, files=_FILES)
        (tmp_path / "a.yaml").write_text("x: 2\n")
        assert (same, sha256_tree(tmp_path, files=_FILES) != before) == (before, True)

    def test_rename_changes_hash(self, tmp_path: Path) -> None:
        """Same bytes under another name is another config."""
        (tmp_path / "a.yaml").write_text("x: 1\n")
        before = sha256_tree(tmp_path, files=_FILES)
        (tmp_path / "a.yaml").rename(tmp_path / "b.yaml")
        assert sha256_tree(tmp_path, files=_FILES) != before


class TestRecord:
    """The record serialises every Ready rule as checked."""

    def test_json_holds_checks_and_utc_time(self) -> None:
        """Checks are listed with their verdict text and the time is ISO UTC."""
        record = DayZeroRecord(
            run_id="r",
            strategy_id="s",
            day_zero_at=_AT,
            package_version="1",
            release="v",
            config_sha256="c",
            preregistration_sha256="p",
            preregistration_signed_sha256="ps",
            data_manifest_sha256="d",
            parameter_fingerprint=_FP,
            archived_ledger=None,
            archived_ledger_sha256=None,
            checks=MANUAL_RULES,
        )
        payload = json.loads(record.to_json())
        assert (payload["day_zero_at"], [c["verdict"] for c in payload["checks"]]) == (
            "2026-10-15T00:00:00+00:00",
            ["confirm by hand"] * 3,
        )

    def test_naive_time_is_refused(self) -> None:
        """A day 0 without a timezone is ambiguous."""
        with pytest.raises(ValueError, match="timezone"):
            DayZeroRecord(
                run_id="r",
                strategy_id="s",
                day_zero_at=datetime(2026, 10, 15),
                package_version="1",
                release="v",
                config_sha256="c",
                preregistration_sha256="p",
                preregistration_signed_sha256="ps",
                data_manifest_sha256="d",
                parameter_fingerprint=_FP,
                archived_ledger=None,
                archived_ledger_sha256=None,
                checks=MANUAL_RULES,
            )
