"""Tests for DomainEvent base class and schema-versioning enforcement (R-4).

Verifies:
- Subclass without __event_schema_version__ raises TypeError at class-definition time
- Subclass with version can be instantiated
- DomainEvent itself has the required fields

Reference: R-4 (A-4.1 through A-4.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

import pytest

from hermes.domain.events.base import DomainEvent, SchemaVersion

_UTC = ZoneInfo("UTC")


class TestSchemaVersionEnforcement:
    """Verify __init_subclass__ raises for missing version."""

    def test_subclass_without_version_raises_type_error(self) -> None:
        """Missing __event_schema_version__ must raise TypeError at definition time."""
        with pytest.raises(TypeError, match="__event_schema_version__"):

            class BadEvent(DomainEvent):
                pass

    def test_subclass_with_non_int_version_raises_type_error(self) -> None:
        """Non-int __event_schema_version__ must also raise TypeError."""
        with pytest.raises(TypeError, match="__event_schema_version__"):

            class BadVersionEvent(DomainEvent):
                __event_schema_version__: ClassVar[SchemaVersion] = "1"  # type: ignore[assignment]

    def test_subclass_with_version_is_accepted(self) -> None:
        """A DomainEvent subclass with an integer version attribute is accepted without raising."""

        @dataclass(frozen=True, slots=True)
        class GoodEvent(DomainEvent):
            __event_schema_version__: ClassVar[int] = 1
            payload: str

        # Must reach here without exception — no assertion needed beyond this point
        assert GoodEvent.__event_schema_version__ == 1


class TestDomainEventInstantiation:
    """Verify a valid subclass can be instantiated with required fields."""

    def test_instantiation_with_required_fields(self) -> None:
        """A valid DomainEvent subclass can be instantiated with occurred_at and correlation_id."""

        @dataclass(frozen=True, slots=True)
        class ConcreteEvent(DomainEvent):
            __event_schema_version__: ClassVar[int] = 1

        dt = datetime(2026, 8, 23, 14, 22, 3, tzinfo=_UTC)
        event = ConcreteEvent(occurred_at=dt, correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1")
        assert event.occurred_at == dt
        assert event.correlation_id == "01J9Z8T3M5BQR2FVKN4XWY6PH1"

    def test_event_is_frozen(self) -> None:
        """DomainEvent subclasses are immutable — mutating a field after construction raises."""

        @dataclass(frozen=True, slots=True)
        class FrozenEvent(DomainEvent):
            __event_schema_version__: ClassVar[int] = 1

        dt = datetime(2026, 8, 23, 14, 22, 3, tzinfo=_UTC)
        event = FrozenEvent(occurred_at=dt, correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1")
        with pytest.raises((AttributeError, TypeError)):
            event.correlation_id = "changed"  # type: ignore[misc]

    def test_schema_version_is_class_var(self) -> None:
        """__event_schema_version__ is a ClassVar accessible on the class and on type(instance)."""

        @dataclass(frozen=True, slots=True)
        class VersionedEvent(DomainEvent):
            __event_schema_version__: ClassVar[int] = 2

        dt = datetime(2026, 8, 23, 14, 22, 3, tzinfo=_UTC)
        event = VersionedEvent(occurred_at=dt, correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1")
        # Access via class, not instance field
        assert VersionedEvent.__event_schema_version__ == 2
        assert type(event).__event_schema_version__ == 2


class TestSchemaVersionTypeAlias:
    """Verify SchemaVersion is exported and is the int type alias."""

    def test_schema_version_is_int(self) -> None:
        """SchemaVersion is a type alias for int — values of that type are instances of int."""
        v: SchemaVersion = 1
        assert isinstance(v, int)
