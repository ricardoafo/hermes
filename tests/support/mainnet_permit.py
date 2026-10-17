"""A real MainnetPermit for adapter tests, issued through the production path.

Tests never reach the private mint key: they pass the same tier, kill-switch
and live-approval checks a composition root would, over the repository's
config and an in-memory LIVE_APPROVED record. Nothing here touches a network.
"""

from pathlib import Path

from hermes.application.calibration.certification_gates import CertificationRecord
from hermes.application.calibration.lifecycle import CertificationState
from hermes.infrastructure.config import load_config
from hermes.infrastructure.mainnet_permit import MainnetPermit, issue_mainnet_permit

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
_STRATEGY = "test-strategy"
_FINGERPRINT = "test-fingerprint"


def issued_permit() -> MainnetPermit:
    """Return a permit issued for a prod config with a LIVE_APPROVED record."""
    config = load_config(_REPO_CONFIG, env={"HERMES_ENV": "prod", "HERMES_ALLOW_MAINNET": "yes"})
    record = CertificationRecord(
        _STRATEGY,
        state=CertificationState.LIVE_APPROVED,
        parameter_fingerprint=_FINGERPRINT,
    )
    return issue_mainnet_permit(config, strategy=_STRATEGY, record=record, fingerprint=_FINGERPRINT)
