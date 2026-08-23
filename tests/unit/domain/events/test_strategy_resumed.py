"""Tests for the StrategyResumed domain event.

Verifies:
- Schema version is 1 (initial release).
- Frozen semantics.
- Every field round-trips.

Reference: code-conventions.md §10 + §3.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hermes.domain.events.strategy_resumed import StrategyResumed


def _sample_event(*, pause_duration_seconds: int = 900) -> StrategyResumed:
    """Return a deterministic StrategyResumed instance for reuse across tests."""
    return StrategyResumed(
        occurred_at=datetime(2026, 8, 23, 14, 37, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        strategy_id="swing.v1",
        resumed_at=datetime(2026, 8, 23, 14, 37, 3, tzinfo=UTC),
        pause_duration_seconds=pause_duration_seconds,
    )


class TestStrategyResumedSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_one(self) -> None:
        """StrategyResumed.__event_schema_version__ pins to 1 for the initial release."""
        assert StrategyResumed.__event_schema_version__ == 1


class TestStrategyResumedInstantiation:
    """Verify construction populates every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument reaches the instance with the value passed."""
        event = _sample_event()
        assert event.strategy_id == "swing.v1"
        assert event.resumed_at == datetime(2026, 8, 23, 14, 37, 3, tzinfo=UTC)
        assert event.pause_duration_seconds == 900

    def test_event_is_frozen(self) -> None:
        """StrategyResumed is a frozen dataclass — mutation raises."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.strategy_id = "changed"  # type: ignore[misc]

    def test_equality_is_by_value(self) -> None:
        """Two StrategyResumed instances with identical fields compare equal."""
        assert _sample_event() == _sample_event()

    def test_pause_duration_can_be_zero(self) -> None:
        """A same-second resume yields duration 0 — the field allows it."""
        event = _sample_event(pause_duration_seconds=0)
        assert event.pause_duration_seconds == 0
