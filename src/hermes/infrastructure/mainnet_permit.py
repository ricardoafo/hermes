"""Mainnet permit: the only key that lets an exchange adapter trade real capital.

A trading adapter's config used to take ``allow_mainnet: bool``, which any
caller could set, and nothing tied it to :attr:`HermesConfig.allow_mainnet`,
the env tier or the LIVE_APPROVED record (issue #781). The adapters now take
a :class:`MainnetPermit` instead, and :func:`issue_mainnet_permit` is the one
place that makes one, after all three checks pass.

Python cannot make a constructor truly private; the mint key below makes a
permit impossible to create by accident or by a stray ``**overrides``, which
is the failure the audit found. A permit also refuses pickling, so it cannot
be saved and loaded into a process that never ran the checks, and a unit test
fails if any other module under src/ names the mint key. Deliberate misuse
beyond that stays a code-review matter.

Reference: ADR-014 (live approval), ADR-003 §00 I-08, AGENTS.md rule 7.
"""

from dataclasses import InitVar, dataclass
from typing import Final, final

from hermes.application.calibration.certification_gates import (
    CertificationRecord,
    require_live_approval,
)
from hermes.infrastructure.config.root import HermesConfig
from hermes.infrastructure.env_gate import EnvGateError, require_env

__all__ = ["MainnetPermit", "issue_mainnet_permit"]

_MINT_KEY: Final = object()


@final
@dataclass(frozen=True, slots=True)
class MainnetPermit:
    """Proof that this process passed every real-capital check for one strategy.

    Attributes:
        strategy: The strategy whose LIVE_APPROVED record was checked.
    """

    strategy: str
    # InitVar, so the key is checked and never stored: a permit holds nothing
    # that could mint a second one.
    _key: InitVar[object]

    def __post_init__(self, _key: object) -> None:
        """Refuse construction outside :func:`issue_mainnet_permit`.

        Raises:
            TypeError: The mint key is not the module's own.
        """
        if _key is not _MINT_KEY:
            raise TypeError("a MainnetPermit is made only by issue_mainnet_permit")

    def __reduce__(self) -> str | tuple[object, ...]:
        """Refuse pickling: a permit proves checks passed in this process only.

        Raises:
            TypeError: Always.
        """
        raise TypeError("a MainnetPermit cannot be pickled; issue one in each process")


def issue_mainnet_permit(
    config: HermesConfig,
    *,
    strategy: str,
    record: CertificationRecord | None,
    fingerprint: str,
) -> MainnetPermit:
    """Return a permit when the tier, the kill switch and live approval all allow real capital.

    Args:
        config: The loaded configuration; its ``env`` and ``allow_mainnet``
            are checked.
        strategy: The strategy that would trade.
        record: The strategy's stored certification record, or ``None``.
        fingerprint: Fingerprint of the parameters that would trade.

    Returns:
        A permit naming ``strategy``.

    Raises:
        EnvGateError: The tier is not prod, ``allow_mainnet`` is off, or the
            record is not LIVE_APPROVED with unchanged parameters.
    """
    require_env(current=config.env, allowed=("prod",), component="mainnet trading")
    if not config.allow_mainnet:
        raise EnvGateError("mainnet trading needs HERMES_ALLOW_MAINNET=yes")
    refusal = require_live_approval(record, fingerprint)
    if refusal:
        raise EnvGateError(f"mainnet trading for {strategy} refused: {refusal}")
    return MainnetPermit(strategy, _MINT_KEY)
