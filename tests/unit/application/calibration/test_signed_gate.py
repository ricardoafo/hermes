"""Tests for the generic gate's result types (ADR-026 §4, contract §3)."""

import pytest

from hermes.application.calibration.signed_gate import CheckResult, CheckStatus, GateResult


def _result(statuses: list[CheckStatus], *, passed: bool) -> GateResult:
    checks = tuple(CheckResult(f"c{i}", s, "") for i, s in enumerate(statuses))
    return GateResult("swing-alpha", "train", checks, dsr=None, passed=passed)


@pytest.mark.parametrize("statuses", [["PASS"], ["PASS", "NA"], ["NA", "PASS", "PASS"]])
def test_a_verdict_passes_when_no_check_fails(statuses: list[CheckStatus]) -> None:
    """NA counts as no objection: validation_vs_train is NA on a Train-only split."""
    assert _result(statuses, passed=True).passed is True


@pytest.mark.parametrize("statuses", [["FAIL"], ["PASS", "FAIL"], ["NA", "FAIL"]])
def test_a_passed_verdict_with_a_failed_check_is_refused(statuses: list[CheckStatus]) -> None:
    """One FAIL fails the gate; a result that claims otherwise is a bug, not a verdict."""
    with pytest.raises(ValueError, match="passed=True"):
        _result(statuses, passed=True)


def test_a_failed_verdict_with_no_failed_check_is_refused() -> None:
    """A gate cannot fail a candidate without naming the check that failed."""
    with pytest.raises(ValueError, match="passed=False"):
        _result(["PASS", "NA"], passed=False)


def test_a_verdict_with_no_checks_cannot_pass() -> None:
    """A gate that ran nothing has passed nothing."""
    with pytest.raises(ValueError, match="passed=True"):
        _result([], passed=True)
