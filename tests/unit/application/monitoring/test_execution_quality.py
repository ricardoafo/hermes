"""Unit tests for the execution-quality monitor (#362)."""

from __future__ import annotations

from datetime import UTC, datetime

from hermes.application.monitoring.execution_quality import (
    ExecutionObservation,
    ExecutionQualityMonitor,
)
from hermes.domain.events.anomaly_detected import AnomalySeverity

_AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_CID = "01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _normal(i: int) -> ExecutionObservation:
    jitter = (i % 5 - 2) * 0.5
    return ExecutionObservation(fee_bps=10.0 + jitter, slippage_bps=3.0 + jitter, latency_ms=200.0)


def _warm(monitor: ExecutionQualityMonitor) -> None:
    for i in range(30):
        assert monitor.observe(_normal(i), at=_AT, correlation_id=_CID) == []


def test_slippage_breach_emits_one_warning_for_that_metric() -> None:
    """A slippage jump emits one WARNING anomaly sourced to slippage only."""
    monitor = ExecutionQualityMonitor()
    _warm(monitor)
    events = []
    for i in range(20):
        obs = _normal(i)
        events += monitor.observe(
            ExecutionObservation(obs.fee_bps, 25.0, obs.latency_ms), at=_AT, correlation_id=_CID
        )
    assert [(e.source, e.severity) for e in events] == [
        ("execution_quality.slippage_bps", AnomalySeverity.WARNING)
    ]


def test_reason_names_metric_and_units_without_pnl() -> None:
    """The alert text names the metric in bps and carries no P&L wording (ruling 1)."""
    monitor = ExecutionQualityMonitor()
    _warm(monitor)
    events = monitor.observe(
        ExecutionObservation(fee_bps=60.0, slippage_bps=3.0, latency_ms=200.0),
        at=_AT,
        correlation_id=_CID,
    )
    reason = events[0].reason
    assert reason.startswith("fee_bps out of control")
    assert "bps" in reason
    assert not any(word in reason.lower() for word in ("pnl", "p&l", "profit", "equity", "sharpe"))


def test_latency_breach_is_independent_of_other_metrics() -> None:
    """Each metric has its own chart: a latency spike alerts only latency."""
    monitor = ExecutionQualityMonitor()
    _warm(monitor)
    events = monitor.observe(
        ExecutionObservation(fee_bps=10.0, slippage_bps=3.0, latency_ms=5_000.0),
        at=_AT,
        correlation_id=_CID,
    )
    assert [e.source for e in events] == ["execution_quality.latency_ms"]
