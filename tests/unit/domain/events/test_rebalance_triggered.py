"""Tests for the RebalanceTriggered domain event.

Verifies:
- Schema version is 1.
- Frozen semantics.
- Equality by value.
- Weight fields accept the full [0, 1] range documented by Weight.

Reference: code-conventions.md §10 + §3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.weight import Weight


def _sample_event() -> RebalanceTriggered:
    """Return a deterministic RebalanceTriggered instance."""
    return RebalanceTriggered(
        occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        current_weight=Weight(value=Decimal("0.55")),
        target_weight=Weight(value=Decimal("0.40")),
        band=Weight(value=Decimal("0.10")),
    )


class TestRebalanceTriggeredSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_one(self) -> None:
        """RebalanceTriggered.__event_schema_version__ pins to 1 for the initial release."""
        assert RebalanceTriggered.__event_schema_version__ == 1


class TestRebalanceTriggeredInstantiation:
    """Verify construction preserves every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument reaches the instance with the exact value passed."""
        event = _sample_event()
        assert event.pair.base == Currency.BTC
        assert event.pair.quote == Currency.USDT
        assert event.current_weight.value == Decimal("0.55")
        assert event.target_weight.value == Decimal("0.40")
        assert event.band.value == Decimal("0.10")

    def test_event_is_frozen(self) -> None:
        """RebalanceTriggered is a frozen dataclass — mutation raises."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.correlation_id = "changed"  # type: ignore[misc]

    def test_equality_is_by_value(self) -> None:
        """Two RebalanceTriggered instances with identical fields compare equal."""
        assert _sample_event() == _sample_event()

    def test_inequality_when_band_differs(self) -> None:
        """Changing only the band field breaks equality — band is load-bearing."""
        a = _sample_event()
        b = RebalanceTriggered(
            occurred_at=a.occurred_at,
            correlation_id=a.correlation_id,
            pair=a.pair,
            current_weight=a.current_weight,
            target_weight=a.target_weight,
            band=Weight(value=Decimal("0.20")),
        )
        assert a != b
