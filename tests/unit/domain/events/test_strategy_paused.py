"""Tests for the StrategyPaused domain event.

Verifies:
- Schema version is 1 (initial release).
- Frozen semantics.
- Every field round-trips.
- Severity accepts every ``AnomalySeverity`` member.

Reference: code-conventions.md §10 + §3.
"""

from datetime import UTC, datetime

import pytest

from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.events.strategy_paused import StrategyPaused


def _sample_event(
    *,
    severity: AnomalySeverity = AnomalySeverity.CRITICAL,
) -> StrategyPaused:
    """Return a deterministic StrategyPaused instance for reuse across tests."""
    return StrategyPaused(
        occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        strategy_id="swing.v1",
        reason="30% drawdown breached",
        paused_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        severity=severity,
    )


class TestStrategyPausedSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_one(self) -> None:
        """StrategyPaused.__event_schema_version__ pins to 1 for the initial release."""
        assert StrategyPaused.__event_schema_version__ == 1


class TestStrategyPausedInstantiation:
    """Verify construction populates every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument reaches the instance with the value passed."""
        event = _sample_event()
        assert event.strategy_id == "swing.v1"
        assert event.reason == "30% drawdown breached"
        assert event.severity == AnomalySeverity.CRITICAL
        assert event.paused_at == datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)
        assert event.correlation_id == "01J9Z8T3M5BQR2FVKN4XWY6PH1"

    def test_event_is_frozen(self) -> None:
        """StrategyPaused is a frozen dataclass — mutation raises."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.reason = "changed"  # type: ignore[misc]

    def test_equality_is_by_value(self) -> None:
        """Two StrategyPaused instances with identical fields compare equal."""
        assert _sample_event() == _sample_event()


class TestStrategyPausedSeverity:
    """Every AnomalySeverity member is a valid severity for a pause."""

    @pytest.mark.parametrize("severity", list(AnomalySeverity))
    def test_accepts_every_severity_member(self, severity: AnomalySeverity) -> None:
        """Constructor accepts CRITICAL, WARNING, and INFO severities without raising."""
        event = _sample_event(severity=severity)
        assert event.severity == severity
