"""Unit tests for ``hermes certify`` (#355).

``run`` is driven with a JSON store in ``tmp_path`` and a fixed clock; ``main``
is driven through argv for the parsing and exit-code contract.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.application.calibration.certification_gates import (
    LIVE_APPROVAL_PHRASE,
    CertificationRecord,
    GateEvidence,
    ShadowEvidence,
)
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.calibration.shadow_parity import MetricStatus
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.entrypoints import certify as certify_module
from hermes.entrypoints.certify import EvidenceError, load_evidence, run

_SID = "swing-donchian-4h"
_FP = "f" * 64
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_METRICS = {
    "dsr": 0.8,
    "bootstrap_ci_lower": 0.1,
    "trade_count": 40,
    "max_drawdown": 0.1,
    "recovery_days": 30,
    "profit_factor": 1.5,
    "ulcer_index": 6.0,
    "fees_to_gross_pnl": 0.2,
    "win_rate": 0.4,
}


def _evidence_file(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(store: JsonCertificationStore, evidence: GateEvidence, at: datetime) -> tuple[int, str]:
    out = io.StringIO()
    code = run(_SID, evidence, store=store, fingerprint=_FP, stream=out, clock=lambda: at)
    return code, out.getvalue()


class TestRun:
    """`run` evaluates, prints every check and records only what it should."""

    def test_passed_gate_is_recorded(self, tmp_path: Path) -> None:
        """A full backtest evidence set records BACKTEST_CERTIFIED and exits 0."""
        store = JsonCertificationStore(tmp_path)
        evidence = load_evidence(
            _evidence_file(tmp_path, {"backtest": {**_METRICS, "calibration_fill_count": 40}})
        )
        code, out = _run(store, evidence, datetime(2026, 10, 3, tzinfo=UTC))
        record = store.load(_SID)
        assert code == 0
        assert record is not None
        assert record.state == CertificationState.BACKTEST_CERTIFIED
        assert record.transitions[-1].evidence_sha256 == evidence.sha256
        assert "GATE PASSED" in out

    # SEV-1: a failed gate must not move the record (pre-registration §7)
    def test_failed_gate_prints_each_check_and_records_nothing(self, tmp_path: Path) -> None:
        """No evidence prints eight FAIL lines, exits 1 and writes no file."""
        store = JsonCertificationStore(tmp_path)
        code, out = _run(store, GateEvidence(), datetime(2026, 10, 3, tzinfo=UTC))
        assert code == 1
        assert out.count("FAIL ") == 8
        assert "not computed (#354)" in out
        assert store.load(_SID) is None

    # SEV-1: the OOS gate refuses before 2027-01-01 without spending the shot (§4)
    def test_oos_refused_before_2027_leaves_record(self, tmp_path: Path) -> None:
        """From SHADOW_CERTIFIED before 2027 the command refuses and keeps the record."""
        store = JsonCertificationStore(tmp_path)
        record = CertificationRecord(
            _SID, CertificationState.SHADOW_CERTIFIED, parameter_fingerprint=_FP
        )
        store.save(record)
        evidence = load_evidence(_evidence_file(tmp_path, {"oos": _METRICS}))
        code, out = _run(store, evidence, datetime(2026, 12, 31, tzinfo=UTC))
        assert code == 1
        assert "REFUSED" in out
        assert store.load(_SID) == record

    # SEV-1: D-8 guardrail — changed parameters are reverted before any gate
    def test_changed_parameters_revert_and_persist(self, tmp_path: Path) -> None:
        """A record certified under other parameters is reverted and saved as UNTRUSTED."""
        store = JsonCertificationStore(tmp_path)
        store.save(
            CertificationRecord(
                _SID, CertificationState.SHADOW_CERTIFIED, parameter_fingerprint="0" * 64
            )
        )
        code, out = _run(store, GateEvidence(), datetime(2026, 10, 3, tzinfo=UTC))
        record = store.load(_SID)
        assert code == 1
        assert "REVERTED" in out
        assert record is not None
        assert record.state == CertificationState.UNTRUSTED


class TestLoadEvidence:
    """The evidence file is parsed strictly; absent values stay absent."""

    def test_absent_sections_are_none(self, tmp_path: Path) -> None:
        """An empty object yields no evidence at all."""
        evidence = load_evidence(_evidence_file(tmp_path, {}))
        assert (evidence.backtest, evidence.shadow, evidence.oos, evidence.live) == (
            None,
            None,
            None,
            None,
        )

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"forecast": {}},
            {"oos": {"sharpe_guess": 1.0}},
            {"oos": {"dsr": "0.8"}},
            {"oos": {"trade_count": 3.5}},
            {"shadow": {"kill_fired": "no"}},
            {"oos": {"dsr": True}},
        ],
    )
    def test_malformed_evidence_is_rejected(self, tmp_path: Path, payload: object) -> None:
        """Wrong shapes, unknown keys and wrong types are errors, never zeros."""
        with pytest.raises(EvidenceError):
            load_evidence(_evidence_file(tmp_path, payload))


class TestMain:
    """Argument handling and exit codes."""

    def test_unknown_strategy_is_a_usage_error(self) -> None:
        """Only the pre-registered strategy can be certified."""
        out, err = io.StringIO(), io.StringIO()
        assert certify_module.main(["balancer"], stream=out, err=err) == 2
        assert (out.getvalue(), "pre-registered" in err.getvalue()) == ("", True)

    def test_missing_strategy_is_a_usage_error(self) -> None:
        """The strategy id is required."""
        assert certify_module.main([], stream=io.StringIO()) == 2

    def test_bad_evidence_exits_three(self, tmp_path: Path) -> None:
        """A malformed evidence file exits 3."""
        path = _evidence_file(tmp_path, [])
        out, err = io.StringIO(), io.StringIO()
        code = certify_module.main(
            [
                _SID,
                "--evidence",
                str(path),
                "--config-dir",
                str(_PROJECT_ROOT / "config"),
                "--store-dir",
                str(tmp_path / "store"),
            ],
            stream=out,
            err=err,
        )
        assert (code, out.getvalue()) == (3, "")
        assert "evidence" in err.getvalue()

    def test_end_to_end_fails_closed_today(self, tmp_path: Path) -> None:
        """With the shipped config and no evidence, the gate fails and nothing is stored."""
        store_dir = tmp_path / "store"
        code = certify_module.main(
            [_SID, "--config-dir", str(_PROJECT_ROOT / "config"), "--store-dir", str(store_dir)],
            stream=io.StringIO(),
        )
        assert code == 1
        assert not store_dir.exists()


class _Terminal(io.StringIO):
    """A stdin that reports itself as an interactive terminal."""

    def isatty(self) -> bool:
        return True


def _sign_off_argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        _SID,
        "--config-dir",
        str(_PROJECT_ROOT / "config"),
        "--store-dir",
        str(tmp_path / "store"),
        "--sign-off",
        "Ricardo",
        *extra,
    ]


class TestLiveSignOff:
    """Live approval is typed on an interactive terminal, never passed as a flag (#949)."""

    def test_sign_off_without_a_terminal_approves_nothing(self, tmp_path: Path) -> None:
        """A piped or scripted stdin is refused with exit 2 before any gate runs or is stored."""
        out, err = io.StringIO(), io.StringIO()
        piped = io.StringIO(f"{LIVE_APPROVAL_PHRASE}\n")

        code = certify_module.main(_sign_off_argv(tmp_path), stream=out, err=err, stdin=piped)

        assert (code, out.getvalue()) == (2, "")
        assert "interactive terminal" in err.getvalue()
        assert not (tmp_path / "store").exists()

    def test_the_old_confirm_flag_is_refused(self, tmp_path: Path) -> None:
        """``--confirm LIVE_APPROVED`` no longer exists, so a script cannot pass the phrase."""
        argv = _sign_off_argv(tmp_path, "--confirm", LIVE_APPROVAL_PHRASE)
        err = io.StringIO()

        code = certify_module.main(argv, stream=io.StringIO(), err=err, stdin=_Terminal())

        assert code == 2
        assert "could not parse" in err.getvalue()

    @pytest.mark.parametrize("typed", [LIVE_APPROVAL_PHRASE, "live_approved", "yes"])
    def test_the_typed_line_is_the_recorded_confirmation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: str
    ) -> None:
        """What the operator types becomes the sign-off confirmation, after a prompt naming it.

        ``run`` is replaced to capture its evidence; that the gate accepts only
        the exact phrase is tested at the seam in
        ``test_certification_gates.py::TestLiveApprovalGate``.
        """
        seen: list[GateEvidence] = []

        def capture(_sid: str, evidence: GateEvidence, **_: object) -> int:
            seen.append(evidence)
            return 0

        monkeypatch.setattr(certify_module, "run", capture)
        out = io.StringIO()

        code = certify_module.main(
            _sign_off_argv(tmp_path), stream=out, err=io.StringIO(), stdin=_Terminal(f"{typed}\n")
        )

        assert code == 0
        assert f"Type {LIVE_APPROVAL_PHRASE} to confirm" in out.getvalue()
        assert seen[0].sign_off is not None
        assert (seen[0].sign_off.name, seen[0].sign_off.confirmation) == ("Ricardo", typed)


def _strong_performance(window: str) -> dict[str, object]:
    """120 days of steady gains, 30 winners and 10 losers, 5 logged trials."""
    equity = [5000.0]
    for i in range(119):
        equity.append(equity[-1] * (1.003 if i % 2 == 0 else 1.001))
    return {
        "window": window,
        "daily_equity": equity,
        "trades": [{"gross_pnl": 50.0, "fees": 2.0}] * 30
        + [{"gross_pnl": -20.0, "fees": 2.0}] * 10,
        "trials": {"count": 5, "sharpe_variance": 0.0001},
        "bootstrap_seed": 11,
    }


def _performance_file(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "performance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestPerformance:
    """`--performance` computes a window's §9 metrics (#354)."""

    def test_train_val_fills_the_backtest_metrics(self, tmp_path: Path) -> None:
        """A train_val file fills the backtest section and leaves OOS empty."""
        path = _performance_file(tmp_path, _strong_performance("train_val"))
        evidence = certify_module.apply_performance(GateEvidence(), path)
        assert evidence.backtest is not None
        assert evidence.backtest.is_complete()
        assert evidence.oos is None

    def test_oos_fills_the_oos_metrics(self, tmp_path: Path) -> None:
        """An oos file fills the OOS section only."""
        path = _performance_file(tmp_path, _strong_performance("oos"))
        evidence = certify_module.apply_performance(GateEvidence(), path)
        assert (evidence.backtest, evidence.oos is not None) == (None, True)

    def test_hash_covers_both_files(self, tmp_path: Path) -> None:
        """Changing the evidence file changes the recorded hash even with the same performance."""
        path = _performance_file(tmp_path, _strong_performance("train_val"))
        first = certify_module.apply_performance(GateEvidence(sha256="a" * 64), path)
        second = certify_module.apply_performance(GateEvidence(sha256="b" * 64), path)
        assert first.sha256 != second.sha256

    @pytest.mark.parametrize(
        "change",
        [
            {"window": "validation"},
            {"daily_equity": "lots"},
            {"trades": [{"gross_pnl": 1.0}]},
            {"bootstrap_seed": None},
            {"trials": {"count": 0, "sharpe_variance": 0.1}},
            {"daily_equity": [100.0, 0.0]},
            {"extra": 1},
        ],
    )
    def test_malformed_performance_is_rejected(
        self, tmp_path: Path, change: dict[str, object]
    ) -> None:
        """Bad windows, shapes, a missing seed, bad trials or zero equity are errors."""
        path = _performance_file(tmp_path, {**_strong_performance("train_val"), **change})
        with pytest.raises(EvidenceError):
            certify_module.apply_performance(GateEvidence(), path)

    def test_strong_backtest_passes_the_gate(self, tmp_path: Path) -> None:
        """Computed metrics plus a calibration fill certify the backtest end to end."""
        evidence_path = _evidence_file(tmp_path, {"backtest": {"calibration_fill_count": 40}})
        performance_path = _performance_file(tmp_path, _strong_performance("train_val"))
        store_dir = tmp_path / "store"
        out = io.StringIO()
        code = certify_module.main(
            [
                _SID,
                "--evidence",
                str(evidence_path),
                "--performance",
                str(performance_path),
                "--config-dir",
                str(_PROJECT_ROOT / "config"),
                "--store-dir",
                str(store_dir),
            ],
            stream=out,
        )
        record = JsonCertificationStore(store_dir).load(_SID)
        assert code == 0, out.getvalue()
        assert record is not None
        assert record.state == CertificationState.BACKTEST_CERTIFIED


class TestReleaseLayout:
    """On the VPS the pre-registration sits beside ``config/``, not in a repository (#923)."""

    def test_certify_reads_the_pre_registration_beside_the_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An installed package with no repository above it still certifies a release."""
        release = tmp_path / "release"
        shutil.copytree(_PROJECT_ROOT / "config", release / "config")
        experiment = release / "docs" / "experiments" / "swing-mvp1"
        experiment.mkdir(parents=True)
        shutil.copy(
            _PROJECT_ROOT / "docs" / "experiments" / "swing-mvp1" / "pre-registration-v1.md",
            experiment,
        )

        def _no_repository() -> Path:
            raise FileNotFoundError("no pyproject.toml above the installed package")

        monkeypatch.setattr(PreRegistration, "_detect_project_root", staticmethod(_no_repository))
        evidence_path = _evidence_file(tmp_path, {"backtest": {"calibration_fill_count": 40}})
        performance_path = _performance_file(tmp_path, _strong_performance("train_val"))
        store_dir = tmp_path / "store"
        out = io.StringIO()
        code = certify_module.main(
            [
                _SID,
                "--evidence",
                str(evidence_path),
                "--performance",
                str(performance_path),
                "--config-dir",
                str(release / "config"),
                "--store-dir",
                str(store_dir),
            ],
            stream=out,
            err=io.StringIO(),
        )
        record = JsonCertificationStore(store_dir).load(_SID)
        assert (code, record.state if record else None) == (
            0,
            CertificationState.BACKTEST_CERTIFIED,
        ), out.getvalue()

    def test_missing_pre_registration_is_an_error_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No file beside the config and no repository: exit 3 with one ERROR line."""
        release = tmp_path / "release"
        shutil.copytree(_PROJECT_ROOT / "config", release / "config")

        def _no_repository() -> Path:
            raise FileNotFoundError("no pyproject.toml above the installed package")

        monkeypatch.setattr(PreRegistration, "_detect_project_root", staticmethod(_no_repository))
        err = io.StringIO()
        code = certify_module.main(
            [_SID, "--config-dir", str(release / "config"), "--store-dir", str(tmp_path / "s")],
            stream=io.StringIO(),
            err=err,
        )
        assert (code, err.getvalue().startswith("ERROR: pre-registration:")) == (3, True)


_SHADOW = {"days": 90, "drift_p50_bps": 10.0, "kill_fired": False, "drift_breached": False}


def _parity_file(tmp_path: Path, **change: object) -> Path:
    payload = {
        "strategy_id": _SID,
        "since": "2026-10-03T00:00:00+00:00",
        "until": "2027-01-02T00:00:00+00:00",
        "m09": "pass",
        "m10": "pass",
        **change,
    }
    path = tmp_path / "parity.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestShadowParity:
    """`--shadow-parity` feeds M-09 and M-10 to the shadow gate (#965)."""

    def test_the_statuses_join_the_shadow_evidence_already_loaded(self, tmp_path: Path) -> None:
        """The file sets M-09 and M-10 and keeps days, drift and kill from --evidence."""
        loaded = load_evidence(_evidence_file(tmp_path, {"shadow": _SHADOW}))
        path = _parity_file(tmp_path, m10="block")

        evidence = certify_module.apply_shadow_parity(loaded, path, _SID)

        assert evidence.shadow == ShadowEvidence(
            days=90,
            drift_p50_bps=10.0,
            kill_fired=False,
            drift_breached=False,
            m09=MetricStatus.OK,
            m10=MetricStatus.BLOCK,
        )

    def test_the_hash_chains_the_evidence_hash_with_the_parity_file(self, tmp_path: Path) -> None:
        """The recorded hash is sha256(evidence hash + sha256(parity file)), like --performance."""
        path = _parity_file(tmp_path)
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()

        evidence = certify_module.apply_shadow_parity(GateEvidence(sha256="a" * 64), path, _SID)

        assert evidence.sha256 == hashlib.sha256(("a" * 64 + file_hash).encode("ascii")).hexdigest()

    @pytest.mark.parametrize(
        "change",
        [
            {"m09": "ok"},
            {"m10": 0.12},
            {"strategy_id": "swing-mvp1-v2"},
            {"correlation": 0.9},
        ],
    )
    def test_a_malformed_parity_file_is_rejected(
        self, tmp_path: Path, change: dict[str, object]
    ) -> None:
        """An unknown status, a value instead of a status, another strategy, an extra key."""
        with pytest.raises(EvidenceError):
            certify_module.apply_shadow_parity(
                GateEvidence(), _parity_file(tmp_path, **change), _SID
            )

    def test_a_parity_file_without_a_status_is_rejected(self, tmp_path: Path) -> None:
        """Both statuses are required: a file with M-09 only is not a window verdict."""
        path = tmp_path / "parity.json"
        path.write_text(json.dumps({"strategy_id": _SID, "m09": "pass"}), encoding="utf-8")

        with pytest.raises(EvidenceError):
            certify_module.apply_shadow_parity(GateEvidence(), path, _SID)

    def _certify(self, tmp_path: Path, *extra: str) -> tuple[int, str, CertificationRecord | None]:
        store_dir = tmp_path / "store"
        JsonCertificationStore(store_dir).save(
            CertificationRecord(
                _SID,
                CertificationState.BACKTEST_CERTIFIED,
                parameter_fingerprint=certify_module._fingerprint_for(
                    certify_module.load_config(_PROJECT_ROOT / "config"), _SID
                ),
            )
        )
        out = io.StringIO()
        code = certify_module.main(
            [
                _SID,
                "--evidence",
                str(_evidence_file(tmp_path, {"shadow": _SHADOW})),
                "--config-dir",
                str(_PROJECT_ROOT / "config"),
                "--store-dir",
                str(store_dir),
                *extra,
            ],
            stream=out,
        )
        return code, out.getvalue(), JsonCertificationStore(store_dir).load(_SID)

    # SEV-1: the shadow gate must not certify without ADR-021 Done rule 3
    def test_without_the_file_the_shadow_gate_fails_on_both_metrics(self, tmp_path: Path) -> None:
        """90 days, low drift and no kill are not enough: M-09 and M-10 fail as not computed."""
        code, out, record = self._certify(tmp_path)

        assert code == 1
        assert out.count("not computed (--shadow-parity)") == 2
        assert record is not None
        assert record.state == CertificationState.BACKTEST_CERTIFIED

    def test_both_metrics_passed_certify_the_shadow(self, tmp_path: Path) -> None:
        """With the window's pass verdicts the gate records SHADOW_CERTIFIED."""
        code, out, record = self._certify(tmp_path, "--shadow-parity", str(_parity_file(tmp_path)))

        assert code == 0, out
        assert record is not None
        assert record.state == CertificationState.SHADOW_CERTIFIED

    def test_a_bad_parity_file_exits_three(self, tmp_path: Path) -> None:
        """A parity file for another strategy is an evidence error, not a gate verdict."""
        code, out, _ = self._certify(
            tmp_path, "--shadow-parity", str(_parity_file(tmp_path, strategy_id="other"))
        )

        assert (code, out) == (3, "")
