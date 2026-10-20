"""Recharacterization tax guard (ADR-002 §Decision 6).

Pure functions that observe a stream of domain events, compute a rolling
90-day window over the trade activity, and emit :class:`AnomalyDetected`
when any AEAT recharacterization threshold is breached.

The guard is INTENTIONALLY passive at this layer — it computes and emits
one event. Pausing the signal engine on that event is the engine's job
(deferred to #25, the paper-trading engine PR). See :func:`evaluate` for
the composed public entry point.

Reference:

- ADR-002 §Decision 6 — recharacterization risk under Spanish tax rules.
- ``config/tax_guard.yaml`` / :class:`TaxGuardConfig` — threshold values.
- code-conventions.md §1 — Application layer imports domain + ports +
  infra config, never adapters or the ledger implementation.
"""

from hermes.application.tax_guard.window import (
    TaxGuardThresholds,
    WindowStats,
    check_breach,
    compute_window,
    evaluate,
)

__all__ = [
    "TaxGuardThresholds",
    "WindowStats",
    "check_breach",
    "compute_window",
    "evaluate",
]
