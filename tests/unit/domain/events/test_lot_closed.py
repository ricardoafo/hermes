"""Tests for the LotClosed domain event.

Verifies:
- Schema version is 1.
- Frozen semantics.
- Equality by value.
- Realized gain can be negative (loss) — a lot may close underwater.

Reference: code-conventions.md §10 + §3.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.events.lot_closed import LotClosed
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity


def _sample_event(*, realized_amount: Decimal = Decimal("13.50")) -> LotClosed:
    """Return a deterministic LotClosed instance for reuse across tests."""
    return LotClosed(
        occurred_at=datetime(2027, 3, 20, 10, 0, 0, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH2"),
        consumed_qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        realized_gain=Money(amount=realized_amount, currency=Currency.EUR),
    )


class TestLotClosedSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_one(self) -> None:
        """LotClosed.__event_schema_version__ pins to 1 for the initial release."""
        assert LotClosed.__event_schema_version__ == 1


class TestLotClosedInstantiation:
    """Verify construction populates every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument reaches the instance with the exact value passed."""
        event = _sample_event()
        assert event.lot_id.value == "01J9Z8T3M5BQR2FVKN4XWY6PH2"
        assert event.consumed_qty.amount == Decimal("0.001")
        assert event.realized_gain.amount == Decimal("13.50")
        assert event.realized_gain.currency == Currency.EUR

    def test_event_is_frozen(self) -> None:
        """LotClosed is a frozen dataclass — mutation raises."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.correlation_id = "changed"  # type: ignore[misc]

    def test_realized_gain_can_be_negative(self) -> None:
        """A lot may close at a loss — realized_gain accepts negative Money."""
        event = _sample_event(realized_amount=Decimal("-5.25"))
        assert event.realized_gain.amount == Decimal("-5.25")

    def test_equality_is_by_value(self) -> None:
        """Two LotClosed instances with identical fields compare equal."""
        assert _sample_event() == _sample_event()
