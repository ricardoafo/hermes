"""Unit tests for ``hermes certify swing-mvp1-v2`` (#1008, change v2-certify-gate).

``main`` runs against the signed v2 pre-registration and a copy of the shipped
config whose roster holds a v2 entry. Evidence and funnel files are synthetic
and written to ``tmp_path``; no market data is read.
"""

import io
import json
import re
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.application.calibration.certification_gates import v1_parameter_fingerprint
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.entrypoints import certify as certify_module
from tests.support.paths import REPO_ROOT

_V2 = "swing-mvp1-v2"
_START = date(2020, 9, 1)
_DAYS = (date(2025, 8, 31) - _START).days
"""Train + Validation of v2 §4: every day up to the day before the OOS start."""

_STRATEGIES_YAML = """\
strategies:
  - name: swing-donchian-4h
    enabled: true
    weight: "1.0"
    capital_budget: "5000"
    quote: USDT
    sizing:
      risk_pct: "0.01"
      max_position_pct: "0.05"
      max_total_exposure_pct: "0.15"
  - name: swing-mvp1-v2
    enabled: false
    weight: "1.0"
    capital_budget: "4000"
    quote: USDT
    sizing:
      risk_pct: "0.01"
      max_position_pct: "0.05"
      max_total_exposure_pct: "0.15"
"""
"""v2's capital differs from v1's so the fingerprint shows which entry it came from."""

_BACKTEST = {
    "max_drawdown": 0.1,
    "recovery_days": 30,
    "profit_factor": 1.5,
    "ulcer_index": 6.0,
    "fees_to_gross_pnl": 0.2,
    "win_rate": 0.4,
    "calibration_fill_count": 40,
}


def _returns(drift: float) -> list[float]:
    return [drift + (0.001 if i % 2 else -0.001) for i in range(_DAYS)]


def _equity(returns: list[float]) -> list[float]:
    points = [5000.0]
    for r in returns:
        points.append(points[-1] * (1.0 + r))
    return points


def _funnel_evidence(**changes: object) -> dict[str, Any]:
    """Steady gains on every pre-registered stretch: passes all ten funnel checks."""
    base = _returns(0.002)
    payload: dict[str, Any] = {
        "strategy_id": _V2,
        "start_date": _START.isoformat(),
        "daily_equity": _equity(base),
        "trade_count": 140,
        "cost_stress": {"multiplier": 2.0, "daily_equity": _equity(_returns(0.001))},
        "trials": {
            "t1": base,
            "t2": [r + 0.0001 for r in base],
            "t3": [r - 0.0002 for r in base],
            "n1": _returns(0.0015),
        },
        "neighbour_trial_ids": ["n1"],
        "running_strategies": {},
    }
    return {**payload, **changes}


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Return the shipped config with a roster of v1 (enabled) and v2 (disabled)."""
    target = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", target)
    (target / "strategies.yaml").write_text(_STRATEGIES_YAML, encoding="utf-8")
    return target


def _write(tmp_path: Path, name: str, payload: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _certify(
    tmp_path: Path,
    config_dir: Path,
    dsr: float,
    *extra: str,
    funnel: dict[str, Any] | None = None,
) -> tuple[int, str, str]:
    evidence = _write(tmp_path, "evidence.json", {"backtest": {**_BACKTEST, "dsr": dsr}})
    args = [_V2, "--evidence", str(evidence), "--config-dir", str(config_dir)]
    args += ["--store-dir", str(tmp_path / "store"), *extra]
    if funnel is not None:
        args += ["--funnel-evidence", str(_write(tmp_path, "funnel.json", funnel))]
    out, err = io.StringIO(), io.StringIO()
    code = certify_module.main(args, stream=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _line(output: str, name: str) -> str:
    (line,) = (
        x for x in output.splitlines() if x[:6] in ("PASS  ", "FAIL  ") and x.split()[1] == name
    )
    return " ".join(line.split())


def _state(tmp_path: Path) -> CertificationState | None:
    record = JsonCertificationStore(tmp_path / "store").load(_V2)
    return None if record is None else record.state


class TestDsrBoundary:
    """The signed v2 §10 floor: DSR 0.94 fails, 0.95 passes (#1008 acceptance)."""

    def test_dsr_094_fails_and_records_nothing(self, tmp_path: Path, config_dir: Path) -> None:
        """0.94 < 0.95: the dsr line fails, exit 1, no record is written."""
        code, out, _ = _certify(tmp_path, config_dir, 0.94, funnel=_funnel_evidence())
        assert (code, _line(out, "dsr"), _state(tmp_path)) == (
            1,
            "FAIL dsr observed 0.94 required >= 0.95",
            None,
        )

    def test_dsr_095_passes_and_records_backtest_certified(
        self, tmp_path: Path, config_dir: Path
    ) -> None:
        """0.95 meets the floor: the dsr line passes, exit 0, BACKTEST_CERTIFIED is recorded."""
        code, out, _ = _certify(tmp_path, config_dir, 0.95, funnel=_funnel_evidence())
        assert (code, _line(out, "dsr"), _state(tmp_path)) == (
            0,
            "PASS dsr observed 0.95 required >= 0.95",
            CertificationState.BACKTEST_CERTIFIED,
        ), out


class TestOutput:
    """The workflow summary keeps `PASS <check>` / `FAIL <check>` lines only (ADR-023 §7)."""

    def test_every_check_line_keeps_the_certify_layout(
        self, tmp_path: Path, config_dir: Path
    ) -> None:
        """Header, one line per check in the certify layout, then the GATE footer."""
        _, out, _ = _certify(tmp_path, config_dir, 0.95, funnel=_funnel_evidence())
        lines = out.splitlines()
        layout = re.compile(r"^(PASS|FAIL)  \S+ +observed .+ required .+$")
        assert lines[0] == f"# hermes certify {_V2}: UNTRUSTED -> BACKTEST_CERTIFIED"
        assert [bool(layout.match(x)) for x in lines[1:19]] == [True] * 18
        assert lines[19:] == [
            "GATE PASSED",
            "RECORDED: UNTRUSTED -> BACKTEST_CERTIFIED. "
            "Log this transition in the lab notebook (§12).",
        ]

    def test_without_funnel_evidence_the_funnel_row_fails(
        self, tmp_path: Path, config_dir: Path
    ) -> None:
        """v2 §7: BACKTEST_CERTIFIED needs the funnel, so no funnel file means exit 1."""
        code, out, _ = _certify(tmp_path, config_dir, 0.95)
        assert (code, _line(out, "funnel"), _state(tmp_path)) == (
            1,
            "FAIL funnel observed not computed required ADR-021 §5 funnel (--funnel-evidence)",
            None,
        )


class TestRecord:
    """What a passed v2 gate stores."""

    def test_fingerprint_is_the_v2_roster_entry(self, tmp_path: Path, config_dir: Path) -> None:
        """v2's own entry (capital 4000) is fingerprinted, not the enabled v1 entry (change D-4)."""
        _certify(tmp_path, config_dir, 0.95, funnel=_funnel_evidence())
        record = JsonCertificationStore(tmp_path / "store").load(_V2)
        pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        expected = v1_parameter_fingerprint(capital_budget=Decimal("4000"), universe=pairs)
        assert record is not None
        assert record.parameter_fingerprint == expected

    def test_recorded_hash_covers_the_funnel_file(self, tmp_path: Path, config_dir: Path) -> None:
        """Two funnel files with the same verdict record different evidence hashes."""
        hashes = []
        for trades in (140, 150):
            run_dir = tmp_path / str(trades)
            run_dir.mkdir()
            _certify(run_dir, config_dir, 0.95, funnel=_funnel_evidence(trade_count=trades))
            record = JsonCertificationStore(run_dir / "store").load(_V2)
            assert record is not None
            hashes.append(record.transitions[-1].evidence_sha256)
        assert hashes[0] != hashes[1]


class TestErrors:
    """Usage, config, evidence and pre-registration errors keep certify's exit codes."""

    def test_funnel_evidence_is_refused_for_v1(self, tmp_path: Path) -> None:
        """v1 has no funnel: the flag is a usage error (exit 2) for swing-donchian-4h."""
        path = _write(tmp_path, "funnel.json", _funnel_evidence())
        err = io.StringIO()
        code = certify_module.main(
            ["swing-donchian-4h", "--funnel-evidence", str(path)], stream=io.StringIO(), err=err
        )
        assert (code, err.getvalue().splitlines()[0]) == (
            2,
            "ERROR: --funnel-evidence applies to 'swing-mvp1-v2' only.",
        )

    def test_unknown_strategy_names_both_pre_registrations(self) -> None:
        """An unknown id is a usage error naming v1 and v2."""
        err = io.StringIO()
        assert certify_module.main(["balancer"], stream=io.StringIO(), err=err) == 2
        assert err.getvalue() == (
            "ERROR: only 'swing-donchian-4h' (swing-mvp1-v1) and 'swing-mvp1-v2' "
            "(swing-mvp1-v2) are pre-registered; got 'balancer'.\n"
        )

    def test_roster_without_v2_is_a_config_error(self, tmp_path: Path, config_dir: Path) -> None:
        """No swing-mvp1-v2 entry in strategies.yaml: exit 3 naming it (PR #1007 adds it)."""
        only_v1 = _STRATEGIES_YAML.split("  - name: swing-mvp1-v2", maxsplit=1)[0]
        (config_dir / "strategies.yaml").write_text(only_v1, encoding="utf-8")
        code, _, err = _certify(tmp_path, config_dir, 0.95, funnel=_funnel_evidence())
        assert (code, err) == (3, "ERROR: config: strategies.yaml has no 'swing-mvp1-v2' entry\n")

    def test_funnel_evidence_for_another_strategy_is_an_evidence_error(
        self, tmp_path: Path, config_dir: Path
    ) -> None:
        """A funnel file of another strategy exits 3 and records nothing."""
        funnel = _funnel_evidence(strategy_id="swing-donchian-4h")
        code, _, err = _certify(tmp_path, config_dir, 0.95, funnel=funnel)
        assert (code, err, _state(tmp_path)) == (
            3,
            "ERROR: evidence: evidence is for 'swing-donchian-4h', not 'swing-mvp1-v2'\n",
            None,
        )

    def test_funnel_evidence_reaching_oos_is_an_evidence_error(
        self, tmp_path: Path, config_dir: Path
    ) -> None:
        """One more day reaches 2025-09-01, the OOS start: exit 3 (ADR-012, v2 §4)."""
        funnel = _funnel_evidence()
        funnel["daily_equity"] = [*funnel["daily_equity"], funnel["daily_equity"][-1]]
        funnel["cost_stress"]["daily_equity"].append(funnel["cost_stress"]["daily_equity"][-1])
        for series in funnel["trials"].values():
            series.append(0.0)
        code, _, err = _certify(tmp_path, config_dir, 0.95, funnel=funnel)
        assert (code, err) == (
            3,
            "ERROR: evidence: evidence ends on 2025-09-01, which reaches the OOS start "
            "2025-09-01\n",
        )

    def test_missing_v2_file_is_a_pre_registration_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A release with only v1's file beside its config cannot judge v2: exit 3."""
        release = tmp_path / "release"
        shutil.copytree(REPO_ROOT / "config", release / "config")
        (release / "config" / "strategies.yaml").write_text(_STRATEGIES_YAML, encoding="utf-8")
        experiment = release / "docs" / "experiments" / "swing-mvp1"
        experiment.mkdir(parents=True)
        shutil.copy(REPO_ROOT / "docs/experiments/swing-mvp1/pre-registration-v1.md", experiment)

        def _no_repository() -> Path:
            raise FileNotFoundError("no pyproject.toml above the installed package")

        monkeypatch.setattr(PreRegistration, "_detect_project_root", staticmethod(_no_repository))
        code, _, err = _certify(tmp_path, release / "config", 0.95, funnel=_funnel_evidence())
        assert (code, err.startswith("ERROR: pre-registration:")) == (3, True)
        assert "pre-registration-v2.md" in err


class TestShippedConfig:
    """The shipped roster carries swing-mvp1-v2 (PR #1007), so certify can judge it."""

    def test_shipped_config_without_evidence_fails_closed(self, tmp_path: Path) -> None:
        """No evidence: exit 1 with a failing dsr line at v2's floor, and nothing stored."""
        out = io.StringIO()
        code = certify_module.main(
            [_V2, "--config-dir", str(REPO_ROOT / "config"), "--store-dir", str(tmp_path)],
            stream=out,
            err=io.StringIO(),
        )
        assert (code, _line(out.getvalue(), "dsr"), list(tmp_path.iterdir())) == (
            1,
            "FAIL dsr observed not computed (#354) required >= 0.95",
            [],
        )
