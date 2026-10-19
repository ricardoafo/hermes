"""Tests for the BalanceLow domain event.

Verifies:
- Schema version is 1.
- Frozen semantics.
- Every field round-trips.
- Tri-currency invariant: ``asset``, ``current.currency`` and
  ``threshold.currency`` MUST all match; mismatched pairs raise
  ``ValueError`` at construction (invariant I-01).
- ``payload_sha256`` is populated by the base initialiser (#324).

Reference: code-conventions.md §10 + §3.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money


def _sample_event(
    *,
    asset: Currency = Currency.USDT,
    current_amount: Decimal = Decimal("50"),
    threshold_amount: Decimal = Decimal("100"),
) -> BalanceLow:
    """Return a deterministic BalanceLow instance for reuse across tests."""
    return BalanceLow(
        occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        asset=asset,
        current=Money(amount=current_amount, currency=asset),
        threshold=Money(amount=threshold_amount, currency=asset),
        checked_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
    )


class TestBalanceLowSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_one(self) -> None:
        """BalanceLow.__event_schema_version__ pins to 1 for the initial release."""
        assert BalanceLow.__event_schema_version__ == 1


class TestBalanceLowInstantiation:
    """Verify construction populates every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument reaches the instance with the value passed."""
        event = _sample_event()
        assert event.asset == Currency.USDT
        assert event.current.amount == Decimal("50")
        assert event.threshold.amount == Decimal("100")
        assert event.checked_at == datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)

    def test_event_is_frozen(self) -> None:
        """BalanceLow is a frozen dataclass — mutation raises."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.asset = Currency.EUR  # type: ignore[misc]


class TestBalanceLowTriCurrencyInvariant:
    """__post_init__ enforces asset == current.currency == threshold.currency."""

    def test_current_currency_must_match_asset(self) -> None:
        """A ``current`` in a different currency than ``asset`` raises at construction."""
        with pytest.raises(ValueError, match=r"current\.currency"):
            BalanceLow(
                occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
                correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
                asset=Currency.USDT,
                current=Money(amount=Decimal("50"), currency=Currency.EUR),
                threshold=Money(amount=Decimal("100"), currency=Currency.USDT),
                checked_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
            )

    def test_threshold_currency_must_match_asset(self) -> None:
        """A ``threshold`` in a different currency than ``asset`` raises at construction."""
        with pytest.raises(ValueError, match=r"threshold\.currency"):
            BalanceLow(
                occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
                correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
                asset=Currency.USDT,
                current=Money(amount=Decimal("50"), currency=Currency.USDT),
                threshold=Money(amount=Decimal("100"), currency=Currency.EUR),
                checked_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
            )

    def test_matched_trio_constructs_successfully(self) -> None:
        """When asset, current, and threshold agree on currency, construction succeeds."""
        event = _sample_event(asset=Currency.BTC)
        assert event.current.currency == Currency.BTC
        assert event.threshold.currency == Currency.BTC


class TestBalanceLowPayloadHash:
    """BalanceLow runs the base initialiser, so its payload hash is populated (#324)."""

    def test_payload_hash_is_blake2b_hex(self) -> None:
        """payload_sha256 is a 64-char lowercase hex digest, never the empty default."""
        event = _sample_event()
        assert re.fullmatch(r"[0-9a-f]{64}", event.payload_sha256)

    def test_payload_hash_is_stable_for_equal_fields(self) -> None:
        """Two events built from the same fields carry the same hash."""
        assert _sample_event().payload_sha256 == _sample_event().payload_sha256

    def test_payload_hash_tracks_balance(self) -> None:
        """A different current balance yields a different hash."""
        low = _sample_event(current_amount=Decimal("50"))
        lower = _sample_event(current_amount=Decimal("49"))
        assert low.payload_sha256 != lower.payload_sha256
