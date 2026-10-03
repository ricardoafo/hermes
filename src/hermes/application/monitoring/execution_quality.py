"""Execution-quality monitor: control charts on fees, slippage and latency.

Feeds each executed order's fee (bps of notional), slippage (bps from the
decision price) and order latency (ms) into its own
:class:`~hermes.domain.monitoring.control_chart.ControlChart`. The start of a
breach episode becomes an ``AnomalyDetected`` event with ``WARNING``
severity: a human looks, and nothing pauses automatically. Any new pause
rule during v1 would change the pre-registered protocol (§10, §11).

The inputs carry no P&L, equity or position value, only per-order execution
costs in basis points and milliseconds (ruling 1, ADR-012 D-06).

The caller publishes the returned events on the event bus; the notification
router already sends ``ANOMALY_DETECTED`` to its channels. Wiring this into
``hermes serve`` waits for execution logging (#30) and the fill kernel
(#353).

Reference: issue #362; ADR-005 Category B; ADR-012 D-04.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.monitoring.control_chart import Breach, ChartParameters, ControlChart

__all__ = ["SOURCE_PREFIX", "ExecutionObservation", "ExecutionQualityMonitor"]

SOURCE_PREFIX = "execution_quality"
"""``AnomalyDetected.source`` is ``execution_quality.<metric>``."""

_UNITS = {"fee_bps": "bps", "slippage_bps": "bps", "latency_ms": "ms"}


@dataclass(frozen=True, slots=True)
class ExecutionObservation:
    """Execution costs of one order. No P&L, equity or position value."""

    fee_bps: float
    slippage_bps: float
    latency_ms: float


@dataclass
class ExecutionQualityMonitor:
    """One control chart per execution metric."""

    params: ChartParameters = field(default_factory=ChartParameters)
    _charts: dict[str, ControlChart] = field(init=False)

    def __post_init__(self) -> None:
        """Create one chart per metric with the shared parameters."""
        self._charts = {name: ControlChart(self.params) for name in _UNITS}

    def observe(
        self, observation: ExecutionObservation, *, at: datetime, correlation_id: str
    ) -> list[AnomalyDetected]:
        """Feed one order's execution costs into the charts.

        Args:
            observation: The order's fee, slippage and latency.
            at: When the order executed (UTC).
            correlation_id: Correlation id of the order, for the event.

        Returns:
            One ``AnomalyDetected`` per metric whose breach episode starts on
            this order; empty otherwise.
        """
        values = {
            "fee_bps": observation.fee_bps,
            "slippage_bps": observation.slippage_bps,
            "latency_ms": observation.latency_ms,
        }
        events: list[AnomalyDetected] = []
        for name, value in values.items():
            breach = self._charts[name].observe(value)
            if breach is not None:
                events.append(
                    AnomalyDetected(
                        occurred_at=at,
                        correlation_id=correlation_id,
                        source=f"{SOURCE_PREFIX}.{name}",
                        severity=AnomalySeverity.WARNING,
                        reason=_reason(name, breach),
                    )
                )
        return events


def _reason(name: str, breach: Breach) -> str:
    unit = _UNITS[name]
    signals = ", ".join(s.value for s in breach.signals)
    return (
        f"{name} out of control ({signals}): {breach.value:.2f} {unit} against a baseline "
        f"of {breach.baseline_mean:.2f} ± {breach.baseline_sigma:.2f} {unit}"
    )
