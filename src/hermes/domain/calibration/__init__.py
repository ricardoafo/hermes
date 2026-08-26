"""Calibration domain package — metric calculators for fill-model validation.

Pure-function calculators for M-01..M-07 metrics defined in
``docs/experiments/swing-mvp1/pre-registration-v1.md`` §Calibration and SA-5.

These calculators are intentionally domain-layer pure functions — no I/O,
no ports, no application-layer dependencies. The ``CalibrationHarness``
(application layer) collects the raw fill events and calls these calculators
to produce a ``CalibrationReport``.

Reference: spec-6-calibration-harness/change.md.
"""

from hermes.domain.calibration.metrics import (
    FillPriceDeltaBps,
    RegimeReturns,
    compute_m01_fill_count,
    compute_m02_fill_price_delta_bps,
    compute_m03_latency_ms,
    compute_m04_rejection_rate,
    compute_m05_orphan_positions,
    compute_m06_regime_returns,
    compute_m07_queue_quality,
)

__all__ = [
    "FillPriceDeltaBps",
    "RegimeReturns",
    "compute_m01_fill_count",
    "compute_m02_fill_price_delta_bps",
    "compute_m03_latency_ms",
    "compute_m04_rejection_rate",
    "compute_m05_orphan_positions",
    "compute_m06_regime_returns",
    "compute_m07_queue_quality",
]
