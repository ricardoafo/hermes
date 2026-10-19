"""The certification record: one strategy's state in the pre-registration §7 lifecycle.

The states, one recorded transition and the record that holds them are plain
values. They live in the domain so the certification store port and its
adapters can name them without importing the application layer (issue #1186);
the gates that move a record and the state machine stay in
:mod:`hermes.application.calibration`.

Reference: pre-registration-v1.md §7; openspec/archive/355-certification-gates/change.md D-7.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

__all__ = ["CertificationRecord", "CertificationState", "Transition"]


class CertificationState(StrEnum):
    """6+1 states of the certification lifecycle per pre-reg §7.

    Reference: pre-registration-v1.md §7.
    """

    UNTRUSTED = "UNTRUSTED"
    """Default state — no certification evidence."""

    BACKTEST_CERTIFIED = "BACKTEST_CERTIFIED"
    """Backtest evidence collected — at least one fill in the calibration window."""

    SHADOW_CERTIFIED = "SHADOW_CERTIFIED"
    """Shadow-mode evidence collected — operator-triggered after 90+ shadow days."""

    OOS_CERTIFIED = "OOS_CERTIFIED"
    """H1 accepted on OOS window per §9 — operator-triggered after OOS run."""

    LIVE_APPROVED = "LIVE_APPROVED"
    """Manual operator gate + risk overlay green — operator-triggered."""

    CERTIFIED = "CERTIFIED"
    """Full certification — 90+ days live with metrics matching OOS within tolerance."""

    DEAD = "DEAD"
    """Absorbing error state — kill trigger fired. Requires new pre-reg v2."""


@dataclass(frozen=True, slots=True)
class Transition:
    """One recorded state change."""

    from_state: CertificationState
    to_state: CertificationState
    at: datetime
    reason: str
    evidence_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class CertificationRecord:
    """Persisted certification state for one strategy.

    ``parameter_fingerprint`` is the fingerprint the last passed gate saw.
    ``oos_evaluated_at`` is set once the one-shot OOS test has run and is never
    cleared for this pre-registration.
    """

    strategy_id: str
    state: CertificationState = CertificationState.UNTRUSTED
    parameter_fingerprint: str | None = None
    oos_evaluated_at: datetime | None = None
    transitions: tuple[Transition, ...] = field(default=())

    def moved_to(
        self,
        state: CertificationState,
        *,
        at: datetime,
        reason: str,
        fingerprint: str | None,
        evidence_sha256: str | None = None,
    ) -> CertificationRecord:
        """Return a copy in ``state`` with the transition appended."""
        transition = Transition(self.state, state, at, reason, evidence_sha256)
        return replace(
            self,
            state=state,
            parameter_fingerprint=fingerprint,
            transitions=(*self.transitions, transition),
        )
