"""Domain event base class and schema-versioning contract.

Every concrete domain event MUST extend ``DomainEvent`` and declare
``__event_schema_version__: ClassVar[int]``. This is enforced at class-
definition time via ``__init_subclass__`` — missing the attribute raises
``TypeError`` immediately, not at runtime when an event is emitted.

The schema version is written to the ledger by the serializer and read back
by the deserializer to dispatch through the upcaster chain (v1 → v2 → v3).
See code-conventions.md §10 for the full upcaster pattern.

Reference: code-conventions.md §10 — Event schema versioning + upcaster chain.
"""

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

__all__ = ["DomainEvent", "SchemaVersion"]

type SchemaVersion = int
"""Type alias for event schema version integers."""


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Abstract base class for all hermes domain events.

    Subclasses MUST declare ``__event_schema_version__: ClassVar[int]``.
    Omitting it raises ``TypeError`` at class-definition time (not at
    instantiation) so the error surfaces immediately in tests.

    Fields:
        occurred_at: UTC-aware datetime of when the event occurred.
            Always use ``now_utc()`` from ``hermes.infrastructure.time``.
        correlation_id: ULID string (26 chars, Crockford base32) that ties
            this event to a request/cycle. Propagates across all log lines
            in the same async task via ``structlog.contextvars``.
        event_seq: Caller-supplied monotonic sequence number within a
            correlation (>= 0). Used for hash-chain ordering (D-3).
        payload_sha256: BLAKE2b-256 hex digest of the event payload
            (auto-computed in ``__post_init__``, not an init param).

    Reference: code-conventions.md §10, spec-1-domain-aggregates D-2/D-3.
    """

    occurred_at: datetime
    correlation_id: str
    event_seq: int = field(default=0, kw_only=True)
    payload_sha256: str = field(default="", init=False)

    def __post_init__(self) -> None:
        """Validate event_seq and compute payload_sha256 (BLAKE2b-256).

        Raises:
            ValueError: If ``event_seq`` is negative.
        """
        if self.event_seq < 0:
            raise ValueError(f"DomainEvent.event_seq must be >= 0, got {self.event_seq!r}")
        payload = {k: v for k, v in dataclasses.asdict(self).items() if k != "payload_sha256"}
        digest = hashlib.blake2b(
            json.dumps(payload, sort_keys=True, default=str).encode(),
            digest_size=32,
        ).hexdigest()
        object.__setattr__(self, "payload_sha256", digest)

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Enforce ``__event_schema_version__`` at class-definition time.

        Args:
            **kwargs: Passed through to ``super().__init_subclass__``.

        Raises:
            TypeError: If the subclass does not declare
                ``__event_schema_version__`` as a ``ClassVar[int]``.
        """
        super().__init_subclass__(**kwargs)
        # Walk the MRO to check if the class itself (not a parent) defines the version.
        # We skip DomainEvent itself — only concrete subclasses must carry the version.
        if cls is DomainEvent:
            return  # pragma: no cover
        own_attrs = set(cls.__dict__)
        if "__event_schema_version__" not in own_attrs:
            raise TypeError(
                f"{cls.__qualname__} must define "
                f"'__event_schema_version__: ClassVar[int]' — "
                f"see code-conventions.md §10."
            )
        version = cls.__dict__["__event_schema_version__"]
        if not isinstance(version, int):
            raise TypeError(
                f"{cls.__qualname__}.__event_schema_version__ must be an int, "
                f"got {type(version).__name__!r}."
            )

    # Subclasses declare this — DomainEvent itself intentionally does not.
    __event_schema_version__: ClassVar[SchemaVersion]
