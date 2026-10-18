"""Unit tests for ``hermes funnel`` (#957, change v2-backtest-funnel).

Every pre-registration and evidence file is synthetic and written to
``tmp_path``; no market data is read. One test reads the signed v1
pre-registration's front matter to show v1 is refused, not evaluated, and
one reads the signed v2 front matter to show it parses; neither holds market
data.
"""

import hashlib
import io
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from hermes.application.backtest.fold_evidence import fold_evidence
from hermes.application.calibration.backtest_funnel import (
    FUNNEL_CHECK_NAMES,
    Regime,
    parse_funnel_front_matter,
)
from hermes.entrypoints.commands import discover
from hermes.entrypoints.funnel import main, read_front_matter
from tests.support.paths import REPO_ROOT

_SID = "swing-v2"
_DAYS = 730
"""31 Dec 2020 to 31 Dec 2022: Train is 2021, Validation is 2022."""

_FUNNEL_YAML = """\
---
experiment_id: swing-v2
strategy_id: swing-v2
status: SIGNED
backtest_funnel:
  train_end: 2021-12-31
  oos_start: 2025-09-01
  cscv_blocks: 4
  pbo_max: 0.05
  t_stat_above: 3.0
  trade_count_min: 100
  cost_stress_multiplier: 2.0
  positive_year_share_above: 0.5
  validation_to_train_return_min: 0.5
  neighbour_sharpe_ratio_min: 0.5
  return_correlation_below: 0.7
  regimes:
    - {kind: bull, start: 2021-02-01, end: 2021-05-01}
    - {kind: bear, start: 2022-03-01, end: 2022-06-01}
---

# Pre-registration (synthetic test fixture)
"""


def _returns(drift: float) -> list[float]:
    """Alternating returns ``drift ± 0.001``: positive mean, dispersion in every block."""
    return [drift + (0.001 if i % 2 else -0.001) for i in range(_DAYS)]


def _equity(returns: list[float]) -> list[float]:
    points = [5000.0]
    for r in returns:
        points.append(points[-1] * (1.0 + r))
    return points


def _passing_evidence() -> dict[str, Any]:
    base = _returns(0.002)
    neighbour = _returns(0.0015)
    return {
        "strategy_id": _SID,
        "start_date": "2020-12-31",
        "daily_equity": _equity(base),
        "trade_count": 140,
        "cost_stress": {"multiplier": 2.0, "daily_equity": _equity(_returns(0.001))},
        "trials": {
            "t1": base,
            "t2": [r + 0.0001 for r in base],
            "t3": [r - 0.0002 for r in base],
            "n1": neighbour,
        },
        "neighbour_trial_ids": ["n1"],
        "running_strategies": {
            "swing-donchian-4h": [0.001 if i % 4 < 2 else -0.001 for i in range(_DAYS)]
        },
    }


def _write(tmp_path: Path, pre_reg: str, evidence: object) -> tuple[Path, Path]:
    pre_path = tmp_path / "pre-registration-v2.md"
    pre_path.write_text(pre_reg, encoding="utf-8")
    ev_path = tmp_path / "funnel-evidence.json"
    ev_path.write_text(json.dumps(evidence), encoding="utf-8")
    return pre_path, ev_path


def _main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stream=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _args(pre: Path, evidence: Path, *extra: str) -> list[str]:
    """Arguments for a run; ``--expect-sha256`` is the file's own hash unless ``extra`` sets it."""
    if "--expect-sha256" not in extra:
        digest = hashlib.sha256(pre.read_bytes()).hexdigest() if pre.exists() else "0" * 64
        extra = (*extra, "--expect-sha256", digest)
    return [_SID, "--pre-registration", str(pre), "--evidence", str(evidence), *extra]


def _summary_lines(output: str) -> list[str]:
    """Keep what backtest.yml's ``awk '/^(PASS|FAIL) /{print $1, $2}'`` keeps of each check line."""
    return [
        " ".join(line.split()[:2]) for line in output.splitlines() if line[:5] in ("PASS ", "FAIL ")
    ]


class TestPassingRun:
    """A synthetic run that meets every criterion passes the funnel."""

    def test_every_check_passes_and_exits_zero(self, tmp_path: Path) -> None:
        """Ten PASS lines in Design §3 order, the PASSED footer, exit 0."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        code, out, _ = _main(_args(pre, evidence))
        assert code == 0
        assert _summary_lines(out) == [f"PASS {name}" for name in FUNNEL_CHECK_NAMES]
        assert out.splitlines()[-1] == "FUNNEL PASSED"

    def test_header_names_the_pre_registration_hash(self, tmp_path: Path) -> None:
        """The verdict is tied to the exact pre-registration bytes."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        digest = hashlib.sha256(_FUNNEL_YAML.encode("utf-8")).hexdigest()
        _, out, _ = _main(_args(pre, evidence))
        assert (
            out.splitlines()[0]
            == f"# hermes funnel {_SID}: swing-v2 (pre-registration sha256 {digest})"
        )

    def test_check_line_uses_the_certify_layout(self, tmp_path: Path) -> None:
        """`PASS  <name:<24> observed <x:<22> required <rule>`, as hermes certify prints."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        _, out, _ = _main(_args(pre, evidence))
        (line,) = (x for x in out.splitlines() if x.startswith("PASS  trade_count"))
        assert line == f"PASS  {'trade_count':<24} observed {'140':<22} required >= 100"

    # SEV-1: the job summary must carry no observed number (ADR-023 §7, ADR-012 D-06)
    def test_summary_filter_leaves_no_number(self, tmp_path: Path) -> None:
        """After the workflow's awk, check lines hold a verdict and a name only."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        _, out, _ = _main(_args(pre, evidence))
        kept = "".join(_summary_lines(out))
        assert [c for c in kept if c.isdigit()] == []

    def test_upper_case_expected_hash_is_accepted(self, tmp_path: Path) -> None:
        """The signed hash may be written in upper case; it is compared case-blind."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        digest = hashlib.sha256(_FUNNEL_YAML.encode("utf-8")).hexdigest().upper()
        code, _, _ = _main(_args(pre, evidence, "--expect-sha256", digest))
        assert code == 0


class TestFailingRun:
    """A run that misses a criterion fails the funnel with exit 1."""

    def test_one_short_of_the_trade_minimum_fails_only_that_check(self, tmp_path: Path) -> None:
        """99 trades: one FAIL line, nine PASS lines, the FAILED footer."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, {**_passing_evidence(), "trade_count": 99})
        code, out, _ = _main(_args(pre, evidence))
        assert code == 1
        assert [line for line in _summary_lines(out) if line.startswith("FAIL")] == [
            "FAIL trade_count"
        ]
        assert out.splitlines()[-1] == "FUNNEL FAILED"

    def test_absent_sections_fail_closed(self, tmp_path: Path) -> None:
        """Only the base run given: every check that needs more evidence fails."""
        bare = {
            k: v
            for k, v in _passing_evidence().items()
            if k in ("strategy_id", "start_date", "daily_equity")
        }
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, bare)
        code, out, _ = _main(_args(pre, evidence))
        assert code == 1
        assert [line for line in _summary_lines(out) if line.startswith("FAIL")] == [
            "FAIL pbo",
            "FAIL trade_count",
            "FAIL cost_stress",
            "FAIL neighbours",
            "FAIL correlation",
        ]


class TestRefusals:
    """Missing or unusable thresholds refuse the run: exit 1, no check line."""

    # SEV-1: a missing threshold must refuse, never default (issue #957)
    def test_missing_threshold_refuses(self, tmp_path: Path) -> None:
        """No pbo_max: REFUSED naming the key, and no check is printed."""
        text = _FUNNEL_YAML.replace("  pbo_max: 0.05\n", "")
        pre, evidence = _write(tmp_path, text, _passing_evidence())
        code, out, _ = _main(_args(pre, evidence))
        assert code == 1
        assert out.splitlines()[1:] == ["REFUSED: backtest_funnel.pbo_max is missing"]

    def test_v1_pre_registration_refuses(self, tmp_path: Path) -> None:
        """The signed v1 document has no funnel block: ADR-021 §5 does not bind v1."""
        _, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        v1 = REPO_ROOT / "docs/experiments/swing-mvp1/pre-registration-v1.md"
        code, out, _ = _main(_args(v1, evidence))
        assert code == 1
        assert out.splitlines()[1:] == [
            "REFUSED: pre-registration swing-mvp1-v1 has no backtest_funnel block"
        ]

    def test_hash_mismatch_refuses(self, tmp_path: Path) -> None:
        """--expect-sha256 that differs from the file refuses before any threshold is read."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        code, out, _ = _main(_args(pre, evidence, "--expect-sha256", "0" * 64))
        assert code == 1
        assert out.splitlines()[1].startswith("REFUSED: pre-registration sha256 ")
        assert out.splitlines()[1].endswith(f" does not match the expected {'0' * 64}")

    @pytest.mark.parametrize(
        "evidence_text",
        ["{", json.dumps({"strategy_id": "other"})],
        ids=["not-json", "wrong-strategy"],
    )
    def test_hash_mismatch_refuses_before_the_evidence_is_read(
        self, tmp_path: Path, evidence_text: str
    ) -> None:
        """A wrong hash refuses (exit 1) even when the evidence would be an error (exit 3)."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, {})
        evidence.write_text(evidence_text, encoding="utf-8")
        code, out, err = _main(_args(pre, evidence, "--expect-sha256", "0" * 64))
        assert (code, err) == (1, "")
        assert out.splitlines()[1].startswith("REFUSED: pre-registration sha256 ")

    def test_hash_mismatch_refuses_when_the_evidence_file_is_absent(self, tmp_path: Path) -> None:
        """The evidence path is not even opened once the pre-registration is refused."""
        pre, _ = _write(tmp_path, _FUNNEL_YAML, {})
        absent = tmp_path / "absent.json"
        code, out, err = _main(_args(pre, absent, "--expect-sha256", "0" * 64))
        assert (code, err) == (1, "")
        assert out.splitlines()[1].startswith("REFUSED: pre-registration sha256 ")

    def test_hash_mismatch_refuses_a_file_with_no_front_matter(self, tmp_path: Path) -> None:
        """Any file that is not the signed one refuses, whatever it holds."""
        pre, evidence = _write(tmp_path, "# no front matter\n", _passing_evidence())
        code, out, err = _main(_args(pre, evidence, "--expect-sha256", "0" * 64))
        assert (code, err) == (1, "")
        assert out.splitlines()[0].startswith(f"# hermes funnel {_SID}: - (pre-registration")

    def test_missing_threshold_refuses_before_the_evidence_is_read(self, tmp_path: Path) -> None:
        """A signed file without a usable threshold refuses (exit 1) over bad evidence."""
        text = _FUNNEL_YAML.replace("  pbo_max: 0.05\n", "")
        pre, evidence = _write(tmp_path, text, {})
        evidence.write_text("{", encoding="utf-8")
        code, out, err = _main(_args(pre, evidence))
        assert (code, err) == (1, "")
        assert out.splitlines()[1:] == ["REFUSED: backtest_funnel.pbo_max is missing"]

    def test_pre_registration_without_strategy_id_refuses(self, tmp_path: Path) -> None:
        """A pre-registration must name the strategy it binds."""
        text = _FUNNEL_YAML.replace("strategy_id: swing-v2\n", "")
        pre, evidence = _write(tmp_path, text, _passing_evidence())
        code, out, _ = _main(_args(pre, evidence))
        assert (code, out.splitlines()[1:]) == (
            1,
            ["REFUSED: pre-registration swing-v2 names no strategy_id"],
        )

    def test_pre_registration_for_another_strategy_refuses(self, tmp_path: Path) -> None:
        """Thresholds signed for swing-v3 never judge swing-v2."""
        text = _FUNNEL_YAML.replace("strategy_id: swing-v2\n", "strategy_id: swing-v3\n")
        pre, evidence = _write(tmp_path, text, _passing_evidence())
        code, out, _ = _main(_args(pre, evidence))
        assert (code, out.splitlines()[1:]) == (
            1,
            ["REFUSED: pre-registration swing-v2 is for strategy 'swing-v3', not 'swing-v2'"],
        )

    def test_unsigned_pre_registration_refuses(self, tmp_path: Path) -> None:
        """A draft states no binding threshold."""
        pre, evidence = _write(
            tmp_path, _FUNNEL_YAML.replace("SIGNED", "DRAFT"), _passing_evidence()
        )
        code, out, _ = _main(_args(pre, evidence))
        assert (code, out.splitlines()[1]) == (
            1,
            "REFUSED: pre-registration status is 'DRAFT', not SIGNED",
        )


class TestErrors:
    """Unreadable inputs exit 3 with an ERROR line; bad arguments exit 2."""

    # SEV-1: no evidence from the sealed OOS window may be evaluated (ADR-023 §4)
    def test_evidence_reaching_oos_exits_three(self, tmp_path: Path) -> None:
        """A run ending on 1 September 2025 is refused before any check."""
        start = date(2025, 9, 1) - timedelta(days=_DAYS)
        evidence_doc = {**_passing_evidence(), "start_date": start.isoformat()}
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, evidence_doc)
        code, out, err = _main(_args(pre, evidence))
        assert code == 3
        assert "reaches the OOS start 2025-09-01" in err
        assert _summary_lines(out) == []

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"strategy_id": "other"}, "evidence is for 'other', not 'swing-v2'"),
            ({"extra": 1}, "unknown evidence keys: ['extra']"),
            ({"start_date": "31/12/2020"}, "start_date must be an ISO date, got '31/12/2020'"),
            ({"daily_equity": [1.0, True]}, "daily_equity values must be numbers, got True"),
            ({"trade_count": 1.5}, "trade_count must be an integer, got 1.5"),
            (
                {"cost_stress": {"multiplier": 2.0}},
                "cost_stress needs exactly multiplier and daily_equity",
            ),
            ({"trials": {"t1": "x"}}, "trials.t1 must be a list of numbers"),
            ({"neighbour_trial_ids": "n1"}, "neighbour_trial_ids must be a list of strings"),
        ],
    )
    def test_malformed_evidence_exits_three(
        self, tmp_path: Path, change: dict[str, Any], reason: str
    ) -> None:
        """Each malformed field is named in the ERROR line."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, {**_passing_evidence(), **change})
        code, _, err = _main(_args(pre, evidence))
        assert (code, err.strip()) == (3, f"ERROR: evidence: {reason}")

    def test_evidence_that_is_not_json_exits_three(self, tmp_path: Path) -> None:
        """A truncated file is an evidence error."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, {})
        evidence.write_text("{", encoding="utf-8")
        code, _, err = _main(_args(pre, evidence))
        assert code == 3
        assert err.startswith("ERROR: evidence: ")

    def test_pre_registration_without_front_matter_exits_three(self, tmp_path: Path) -> None:
        """A file that does not open with --- has no thresholds to read."""
        pre, evidence = _write(tmp_path, "# no front matter\n", _passing_evidence())
        code, _, err = _main(_args(pre, evidence))
        assert (code, err.strip()) == (
            3,
            "ERROR: pre-registration: no YAML front matter (first line must be ---)",
        )

    def test_missing_pre_registration_file_exits_three(self, tmp_path: Path) -> None:
        """An absent file is a pre-registration error, not a refusal."""
        _, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        code, _, err = _main(_args(tmp_path / "absent.md", evidence))
        assert code == 3
        assert err.startswith("ERROR: pre-registration: ")

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            [_SID, "--evidence", "e.json"],
            [_SID, "--pre-registration", "p.md"],
            [_SID, "--pre-registration", "p.md", "--evidence", "e.json"],
            [
                _SID,
                "--pre-registration",
                "p.md",
                "--evidence",
                "e.json",
                "--expect-sha256",
                "0" * 64,
                "--bogus",
            ],
        ],
    )
    def test_incomplete_arguments_exit_two(self, argv: list[str]) -> None:
        """strategy_id, --pre-registration, --evidence and --expect-sha256 are all required."""
        code, _, err = _main(argv)
        assert code == 2
        assert err.startswith("ERROR: ")

    def test_missing_expected_hash_names_the_option(self, tmp_path: Path) -> None:
        """Without the signed hash the run is a usage error, exit 2, before any file is read."""
        pre, evidence = _write(tmp_path, _FUNNEL_YAML, _passing_evidence())
        code, out, err = _main([_SID, "--pre-registration", str(pre), "--evidence", str(evidence)])
        assert (code, out, err.splitlines()[0]) == (
            2,
            "",
            "ERROR: strategy_id, --pre-registration, --evidence and --expect-sha256 are required.",
        )

    def test_help_exits_zero(self) -> None:
        """--help prints the usage on stdout."""
        code, out, _ = _main(["--help"])
        assert (code, out.splitlines()[0]) == (
            0,
            "Usage: hermes funnel <strategy_id> --pre-registration FILE",
        )


class TestSignedV2PreRegistration:
    """The signed v2 document is what backtest.yml will judge swing-mvp1-v2 against."""

    def test_signed_v2_front_matter_parses(self) -> None:
        """Its funnel block, regimes included, passes the parser that ``hermes funnel`` runs."""
        v2 = REPO_ROOT / "docs/experiments/swing-mvp1/pre-registration-v2.md"
        front = read_front_matter(v2.read_text(encoding="utf-8"))
        thresholds = parse_funnel_front_matter(front, strategy_id="swing-mvp1-v2")
        assert thresholds.regimes == (
            Regime("bull", date(2020, 10, 1), date(2021, 11, 10)),
            Regime("bear", date(2021, 11, 11), date(2022, 11, 21)),
            Regime("bull", date(2022, 11, 22), date(2024, 3, 14)),
        )


class TestRegistration:
    """`hermes funnel` is a registered subcommand."""

    def test_registry_runs_the_command(self) -> None:
        """The registry entry dispatches to this command's help."""
        out = io.StringIO()
        code = discover()["funnel"].run(["--help"], stream=out, err=io.StringIO())
        assert (code, out.getvalue().splitlines()[0]) == (
            0,
            "Usage: hermes funnel <strategy_id> --pre-registration FILE",
        )


class TestFoldEvidenceIsReportedOnly:
    """Pre-registration §8.3 folds in the evidence file never change a funnel verdict (#1181)."""

    @pytest.mark.parametrize(
        "change",
        [{}, {"trade_count": 99}],
        ids=["passing-run", "failing-run"],
    )
    def test_the_output_is_identical_with_and_without_folds(
        self, tmp_path: Path, change: dict[str, Any]
    ) -> None:
        """Same exit code and stdout, check lines and values included, for a pass and a fail."""
        plain = {**_passing_evidence(), **change}
        folded = {**plain, "folds": fold_evidence(date(2020, 12, 31), plain["daily_equity"])}
        (tmp_path / "plain").mkdir()
        (tmp_path / "folds").mkdir()

        without = _main(_args(*_write(tmp_path / "plain", _FUNNEL_YAML, plain)))
        with_folds = _main(_args(*_write(tmp_path / "folds", _FUNNEL_YAML, folded)))

        assert (with_folds[0], with_folds[1]) == (without[0], without[1])

    def test_a_failing_run_with_folds_still_fails(self, tmp_path: Path) -> None:
        """99 trades with folds: exit 1, the comparison above covers a FAIL."""
        evidence = {**_passing_evidence(), "trade_count": 99}
        evidence["folds"] = fold_evidence(date(2020, 12, 31), evidence["daily_equity"])

        code, out, _ = _main(_args(*_write(tmp_path, _FUNNEL_YAML, evidence)))

        assert (code, out.splitlines()[-1]) == (1, "FUNNEL FAILED")
