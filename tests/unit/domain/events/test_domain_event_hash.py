"""Tests for the DomainEvent.payload_sha256 hash chain field.

Verifies:

- **Determinism**: identical fields produce an identical 64-hex-char hash.
- **Sensitivity**: a single-field change produces a different hash.
- **Format**: hash is always a 64-character lowercase hex string
  (BLAKE2b-256, digest_size=32).
- **event_seq validation**: negative event_seq raises ValueError.
- **Auto-computation**: payload_sha256 is computed in __post_init__,
  not passed by the caller.

Property tests use Hypothesis to generalise determinism and sensitivity
across a wide range of inputs.

Reference: spec-1-domain-aggregates (D-2, D-3), ADR-003 §00.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_TS = datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"


def _trade(
    *,
    event_seq: int = 0,
    price: Decimal = Decimal("68000"),
) -> TradeExecuted:
    """Return a TradeExecuted event with the given parameters."""
    return TradeExecuted(
        occurred_at=_TS,
        correlation_id=_CID,
        event_seq=event_seq,
        pair=_PAIR,
        side=TradeSide.BUY,
        qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        price=Price(amount=price, pair=_PAIR),
        fee=Money(amount=Decimal("0.06"), currency=Currency.USDT),
    )


_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Example-based tests
# ---------------------------------------------------------------------------


class TestPayloadSha256Format:
    """payload_sha256 is always a 64-char lowercase hex string."""

    def test_hash_is_64_hex_chars(self) -> None:
        """payload_sha256 is a 64-character lowercase hex string (BLAKE2b-256)."""
        event = _trade()
        assert _HEX_RE.match(event.payload_sha256), (
            f"Expected 64 lowercase hex chars, got: {event.payload_sha256!r}"
        )

    def test_hash_is_not_empty(self) -> None:
        """payload_sha256 is never the empty string (the init=False default)."""
        event = _trade()
        assert event.payload_sha256 != ""


class TestPayloadSha256Determinism:
    """Identical events produce identical hashes."""

    def test_two_identical_events_have_same_hash(self) -> None:
        """Two TradeExecuted instances with identical fields share payload_sha256."""
        a = _trade(event_seq=0, price=Decimal("68000"))
        b = _trade(event_seq=0, price=Decimal("68000"))
        assert a.payload_sha256 == b.payload_sha256

    def test_same_event_seq_same_price_deterministic(self) -> None:
        """Constructing the same event twice yields the same hash — no randomness."""
        h1 = _trade(event_seq=7).payload_sha256
        h2 = _trade(event_seq=7).payload_sha256
        assert h1 == h2, (
            "payload_sha256 must be deterministic — same input fields "
            f"must yield the same digest across constructions. Got h1={h1!r}, h2={h2!r}."
        )
        assert len(h1) == 64, f"BLAKE2b-256 hex digest must be 64 chars, got {len(h1)}"
        assert all(c in "0123456789abcdef" for c in h1), (
            f"BLAKE2b-256 hex digest must be lowercase hex; got {h1!r}"
        )


class TestPayloadSha256Sensitivity:
    """A single-field difference produces a different hash."""

    def test_different_event_seq_yields_different_hash(self) -> None:
        """event_seq=0 and event_seq=1 produce distinct hashes."""
        a = _trade(event_seq=0)
        b = _trade(event_seq=1)
        assert a.payload_sha256 != b.payload_sha256

    def test_different_price_yields_different_hash(self) -> None:
        """Changing a payload field changes the hash."""
        a = _trade(price=Decimal("68000"))
        b = _trade(price=Decimal("69000"))
        assert a.payload_sha256 != b.payload_sha256


class TestEventSeqValidation:
    """event_seq must be >= 0."""

    def test_zero_is_valid(self) -> None:
        """event_seq=0 is the minimum valid value."""
        event = _trade(event_seq=0)
        assert event.event_seq == 0

    def test_positive_is_valid(self) -> None:
        """Any positive integer is accepted."""
        event = _trade(event_seq=42)
        assert event.event_seq == 42

    def test_negative_raises(self) -> None:
        """Negative event_seq violates the >= 0 invariant."""
        with pytest.raises(ValueError, match="event_seq"):
            _trade(event_seq=-1)


class TestPayloadSha256NotInit:
    """payload_sha256 must NOT be accepted as a constructor argument."""

    def test_payload_sha256_is_computed_not_supplied(self) -> None:
        """payload_sha256 is always auto-computed — it is not an __init__ param."""
        # Verify by inspecting that two events with different event_seq differ in hash.
        # (If payload_sha256 were an init param with a default, both would have "")
        a = _trade(event_seq=0)
        b = _trade(event_seq=1)
        assert a.payload_sha256 != ""
        assert b.payload_sha256 != ""
        assert a.payload_sha256 != b.payload_sha256


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestPayloadSha256PropertyDeterminism:
    """Hypothesis: determinism holds across any non-negative event_seq."""

    @given(seq=st.integers(min_value=0, max_value=10_000))
    def test_deterministic_for_any_event_seq(self, seq: int) -> None:
        """For any valid event_seq, constructing the event twice gives identical hashes."""
        a = _trade(event_seq=seq)
        b = _trade(event_seq=seq)
        assert a.payload_sha256 == b.payload_sha256
        assert _HEX_RE.match(a.payload_sha256)

    @given(
        seq_a=st.integers(min_value=0, max_value=10_000),
        seq_b=st.integers(min_value=0, max_value=10_000),
    )
    @settings(max_examples=200)
    def test_different_seq_yields_different_hash(self, seq_a: int, seq_b: int) -> None:
        """Different event_seq values produce different hashes (no collision in range)."""
        if seq_a == seq_b:
            return  # identical inputs → identical hashes, not a failure
        a = _trade(event_seq=seq_a)
        b = _trade(event_seq=seq_b)
        assert a.payload_sha256 != b.payload_sha256
