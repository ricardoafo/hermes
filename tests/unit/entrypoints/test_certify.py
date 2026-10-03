"""Unit tests for ``hermes certify`` (#355).

``run`` is driven with a JSON store in ``tmp_path`` and a fixed clock; ``main``
is driven through argv for the parsing and exit-code contract.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.application.calibration.certification_gates import (
    CertificationRecord,
    GateEvidence,
)
from hermes.application.calibration.lifecycle import CertificationState
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
        """No evidence prints ten FAIL lines, exits 1 and writes no file."""
        store = JsonCertificationStore(tmp_path)
        code, out = _run(store, GateEvidence(), datetime(2026, 10, 3, tzinfo=UTC))
        assert code == 1
        assert out.count("FAIL ") == 10
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
        out = io.StringIO()
        assert certify_module.main(["balancer"], stream=out) == 2
        assert "pre-registered" in out.getvalue()

    def test_missing_strategy_is_a_usage_error(self) -> None:
        """The strategy id is required."""
        assert certify_module.main([], stream=io.StringIO()) == 2

    def test_bad_evidence_exits_three(self, tmp_path: Path) -> None:
        """A malformed evidence file exits 3."""
        path = _evidence_file(tmp_path, [])
        out = io.StringIO()
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
        )
        assert code == 3
        assert "evidence" in out.getvalue()

    def test_end_to_end_fails_closed_today(self, tmp_path: Path) -> None:
        """With the shipped config and no evidence, the gate fails and nothing is stored."""
        store_dir = tmp_path / "store"
        code = certify_module.main(
            [_SID, "--config-dir", str(_PROJECT_ROOT / "config"), "--store-dir", str(store_dir)],
            stream=io.StringIO(),
        )
        assert code == 1
        assert not store_dir.exists()


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
