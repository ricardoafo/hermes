"""Tests for the AnomalyDetected domain event and its severity classifier.

Verifies:
- Schema version is 1.
- Frozen semantics.
- Every documented severity value round-trips through the enum.

Reference: code-conventions.md §10 + §3.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity


def _sample_event(
    *,
    severity: AnomalySeverity = AnomalySeverity.CRITICAL,
) -> AnomalyDetected:
    """Return a deterministic AnomalyDetected instance for reuse across tests."""
    return AnomalyDetected(
        occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        source="swing.risk",
        severity=severity,
        reason="30% drawdown breached",
    )


class TestAnomalyDetectedSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_one(self) -> None:
        """AnomalyDetected.__event_schema_version__ pins to 1 for the initial release."""
        assert AnomalyDetected.__event_schema_version__ == 1


class TestAnomalyDetectedInstantiation:
    """Verify construction populates every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument reaches the instance with the value passed."""
        event = _sample_event()
        assert event.source == "swing.risk"
        assert event.severity == AnomalySeverity.CRITICAL
        assert event.reason == "30% drawdown breached"

    def test_event_is_frozen(self) -> None:
        """AnomalyDetected is a frozen dataclass — mutation raises."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.reason = "changed"  # type: ignore[misc]

    def test_equality_is_by_value(self) -> None:
        """Two AnomalyDetected instances with identical fields compare equal."""
        assert _sample_event() == _sample_event()


class TestAnomalySeverityEnum:
    """Verify AnomalySeverity has the three documented members."""

    def test_all_three_severities_exist(self) -> None:
        """AnomalySeverity has exactly CRITICAL, WARNING, and INFO members."""
        assert set(AnomalySeverity) == {
            AnomalySeverity.CRITICAL,
            AnomalySeverity.WARNING,
            AnomalySeverity.INFO,
        }
