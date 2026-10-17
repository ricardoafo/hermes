"""Tests for the mainnet permit: issued only when tier, kill switch and live approval agree.

Reference: issue #781; ADR-014; AGENTS.md rule 7.
"""

from pathlib import Path

import pytest

from hermes.application.calibration.certification_gates import CertificationRecord
from hermes.application.calibration.lifecycle import CertificationState
from hermes.infrastructure.config import HermesConfig, load_config
from hermes.infrastructure.env_gate import EnvGateError
from hermes.infrastructure.mainnet_permit import MainnetPermit, issue_mainnet_permit

_REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"
_SID = "swing-donchian-4h"
_FP = "fingerprint-a"


def _config(**env: str) -> HermesConfig:
    return load_config(_REPO_CONFIG, env=env)


def _record(state: CertificationState, fingerprint: str = _FP) -> CertificationRecord:
    return CertificationRecord(_SID, state=state, parameter_fingerprint=fingerprint)


_PROD_LIVE = {"HERMES_ENV": "prod", "HERMES_ALLOW_MAINNET": "yes"}


def test_permit_is_issued_for_prod_with_mainnet_on_and_live_approval() -> None:
    """All three checks passing yields a permit naming the strategy."""
    permit = issue_mainnet_permit(
        _config(**_PROD_LIVE),
        strategy=_SID,
        record=_record(CertificationState.LIVE_APPROVED),
        fingerprint=_FP,
    )
    assert permit.strategy == _SID


# SEV-1: real capital stays behind the live-approval gate (AGENTS.md rule 7, ADR-014)
@pytest.mark.parametrize("tier", ["dev", "sandbox", "staging"])
def test_no_permit_outside_prod(tier: str) -> None:
    """A non-prod tier never gets a permit, whatever the record says."""
    with pytest.raises(EnvGateError, match="only usable in env"):
        issue_mainnet_permit(
            _config(HERMES_ENV=tier),
            strategy=_SID,
            record=_record(CertificationState.LIVE_APPROVED),
            fingerprint=_FP,
        )


# SEV-1: real capital stays behind the live-approval gate (AGENTS.md rule 7, ADR-014)
def test_no_permit_in_prod_with_the_kill_switch_off() -> None:
    """Prod without HERMES_ALLOW_MAINNET gets no permit even for a LIVE_APPROVED strategy."""
    with pytest.raises(EnvGateError, match="HERMES_ALLOW_MAINNET"):
        issue_mainnet_permit(
            _config(HERMES_ENV="prod"),
            strategy=_SID,
            record=_record(CertificationState.LIVE_APPROVED),
            fingerprint=_FP,
        )


# SEV-1: real capital stays behind the live-approval gate (AGENTS.md rule 7, ADR-014)
@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (None, "no certification record"),
        (_record(CertificationState.OOS_CERTIFIED), "not LIVE_APPROVED"),
        (_record(CertificationState.LIVE_APPROVED, "fingerprint-b"), "parameters changed"),
    ],
)
def test_no_permit_without_a_matching_live_approval(
    record: CertificationRecord | None, reason: str
) -> None:
    """A missing, unapproved or re-parameterised record gets no permit, and says why."""
    with pytest.raises(EnvGateError, match=reason):
        issue_mainnet_permit(_config(**_PROD_LIVE), strategy=_SID, record=record, fingerprint=_FP)


# SEV-1: real capital stays behind the live-approval gate (AGENTS.md rule 7, ADR-014)
def test_a_permit_cannot_be_constructed_directly() -> None:
    """Calling the constructor with any key but the module's own raises."""
    with pytest.raises(TypeError, match="only by issue_mainnet_permit"):
        MainnetPermit(_SID, object())
