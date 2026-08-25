"""H1AcceptanceGate — §9 threshold evaluator for the H1/H0 certification decision.

Evaluates a :class:`CertificationResult` (pre-computed metrics from the OOS test
run) against the binding thresholds in
``docs/experiments/swing-mvp1/pre-registration-v1.md`` §9 and returns an
:class:`AcceptanceDecision` with a full audit trail of failed criteria.

This module is intentionally stateless — it holds no mutable state between calls.
The thresholds are injected at construction time via
:class:`~hermes.application.strategy_protocol.preregistration.PreRegistration`
to ensure a single source of truth.

H1 is accepted iff ALL of the following hold (§9):

    Primary:
        - DSR on OOS > 0.7
        - Bootstrap 95% CI lower bound on OOS Sharpe > 0
        - OOS trade count >= 30

    Secondary (all must pass):
        - Max drawdown (OOS) < 15%
        - Time-to-recovery from any drawdown >= 8% < 90 days
        - Ulcer index < 10
        - Win rate >= 30%

Reference:
  pre-registration-v1.md §9.
  spec-9-strategy-development-protocol change.md D-4.
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.application.strategy_protocol.preregistration import (
    H1Thresholds,
    PreRegistration,
)

__all__ = [
    "AcceptanceDecision",
    "CertificationResult",
    "H1AcceptanceGate",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CertificationResult:
    """Pre-computed OOS test metrics for H1/H0 evaluation.

    All values must be computed by the certification harness (Spec 6) on the
    OOS test window data. This class carries no computation logic — it is a
    typed snapshot of the harness output.

    Args:
        dsr: Deflated Sharpe Ratio on the OOS window (López de Prado 2014).
        bootstrap_ci_lower: Lower bound of the 95% bootstrap CI on OOS Sharpe.
            Block bootstrap, 10,000 resamples, block length 30 days.
        sharpe: OOS Sharpe ratio (annualised, net of fees).
        max_drawdown: Maximum peak-to-trough drawdown fraction on OOS (e.g. 0.12 = 12%).
        ulcer_index: Ulcer index on the OOS equity curve.
        recovery_days: Maximum number of days to recover from any drawdown >= 8%.
            Use ``float("inf")`` if recovery has not yet occurred.
        win_rate: Fraction of OOS trades that were winners (e.g. 0.45 = 45%).
        oos_trade_count: Number of completed trades in the OOS window.

    Reference: pre-registration-v1.md §9.
    """

    dsr: float
    bootstrap_ci_lower: float
    sharpe: float
    max_drawdown: float
    ulcer_index: float
    recovery_days: float
    win_rate: float
    oos_trade_count: int


@dataclass(frozen=True, slots=True)
class AcceptanceDecision:
    """Result of the H1 acceptance evaluation.

    Args:
        accepted: True iff H1 is accepted (all §9 thresholds passed).
        failed_criteria: Tuple of criterion names that failed. Empty iff
            ``accepted=True``. Criterion names are stable string identifiers
            used by audit logs and tests — do not change without updating tests.

    Criterion name identifiers:
        ``"dsr"`` — DSR threshold
        ``"bootstrap_ci"`` — bootstrap CI lower bound
        ``"oos_trade_count"`` — minimum OOS trade count
        ``"max_drawdown"`` — max drawdown threshold
        ``"recovery_days"`` — recovery period threshold
        ``"ulcer_index"`` — ulcer index threshold
        ``"win_rate"`` — win rate threshold

    Reference: pre-registration-v1.md §9.
    """

    accepted: bool
    failed_criteria: tuple[str, ...]


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class H1AcceptanceGate:
    """Evaluates :class:`CertificationResult` against the signed §9 thresholds.

    Instantiate once with a
    :class:`~hermes.application.strategy_protocol.preregistration.PreRegistration`
    instance; then call :meth:`evaluate` for each certification snapshot.

    Args:
        pre_reg: Pre-registration instance providing the H1 threshold constants.

    Reference: pre-registration-v1.md §9, spec-9 change.md D-4.
    """

    def __init__(self, pre_reg: PreRegistration) -> None:
        """Initialise the gate with thresholds from the signed pre-registration.

        Args:
            pre_reg: Pre-registration accessor providing §9 thresholds.
        """
        self._thresholds: H1Thresholds = pre_reg.h1_thresholds()

    def evaluate(self, result: CertificationResult) -> AcceptanceDecision:
        """Evaluate a :class:`CertificationResult` against §9 thresholds.

        Checks all primary and secondary criteria. H1 is accepted only when
        ALL criteria pass. The full list of failures is always returned so the
        operator can see all issues, not just the first failure.

        Args:
            result: Pre-computed OOS metrics from the certification harness.

        Returns:
            :class:`AcceptanceDecision` with ``accepted`` flag and tuple of
            failed criterion names.

        Reference: pre-registration-v1.md §9.
        """
        t = self._thresholds
        failed: list[str] = []

        # Primary criteria
        if result.dsr <= t.dsr_min:
            failed.append("dsr")

        if result.bootstrap_ci_lower <= t.bootstrap_ci_lower_min:
            failed.append("bootstrap_ci")

        if result.oos_trade_count < t.oos_trade_count_min:
            failed.append("oos_trade_count")

        # Secondary criteria (all must pass per §9)
        if result.max_drawdown >= t.max_drawdown_max:
            failed.append("max_drawdown")

        if result.recovery_days >= t.recovery_days_max:
            failed.append("recovery_days")

        if result.ulcer_index >= t.ulcer_index_max:
            failed.append("ulcer_index")

        if result.win_rate < t.win_rate_min:
            failed.append("win_rate")

        return AcceptanceDecision(
            accepted=len(failed) == 0,
            failed_criteria=tuple(failed),
        )
