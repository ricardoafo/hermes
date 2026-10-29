"""Tests for the ShadowCapitalSeeded domain event (change 405-usdt-fifo D-1).

Reference: ADR-016 item 4.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId

_AT = datetime(2026, 10, 3, 11, 45, tzinfo=UTC)


def _event(amount: str = "10000", at: datetime = _AT) -> ShadowCapitalSeeded:
    return ShadowCapitalSeeded(
        occurred_at=at,
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        strategy_id=StrategyId("swing"),
        amount=Money(amount=Decimal(amount), currency=Currency.USDT),
    )


def test_event_is_versioned_at_one() -> None:
    """Invariant: the first payload shape is schema version 1."""
    assert ShadowCapitalSeeded.__event_schema_version__ == 1


def test_naive_seed_date_is_refused() -> None:
    """Invariant: the seed date is timezone-aware."""
    with pytest.raises(ValueError, match="UTC-aware"):
        _event(at=_AT.replace(tzinfo=None))


def test_non_positive_capital_is_refused() -> None:
    """Invariant: a seed records a positive capital."""
    with pytest.raises(ValueError, match="must be > 0"):
        _event(amount="0")


def test_amount_changes_the_payload_hash() -> None:
    """Invariant: two seeds differing only in amount hash differently."""
    assert _event("10000").payload_sha256 != _event("10001").payload_sha256
