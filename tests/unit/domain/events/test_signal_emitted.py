"""Tests for the SignalEmitted domain event.

Invariants protected here:

- the event is a versioned, frozen, hashed audit record;
- its time is the signal time (the bar close), never a wall clock;
- it mirrors every ``StrategySignal`` field except ``signal_time``, so a field
  added to the signal cannot be silently dropped from the ledger row;
- a naive timestamp is refused.

Reference: 308-signal-event-portfolio-state change.md D-1, D-2, R-1.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_STRATEGY = StrategyId("swing-donchian-4h")
_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_CORRELATION = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
_BAR_CLOSE = datetime(2026, 10, 2, 16, 0, 0, tzinfo=UTC)


def _entry_signal(
    *,
    entry: str = "50000",
    stop: str = "48000",
    atr: str = "1200",
    signal_time: datetime = _BAR_CLOSE,
) -> StrategySignal:
    """Return a long entry signal with the given prices."""
    return StrategySignal(
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        atr_context=Decimal(atr),
        signal_time=signal_time,
    )


def _event(signal: StrategySignal | None = None) -> SignalEmitted:
    """Build an event from ``signal`` (default: a long entry) with fixed ids."""
    return SignalEmitted.from_signal(
        signal or _entry_signal(),
        strategy_id=_STRATEGY,
        pair=_PAIR,
        correlation_id=_CORRELATION,
    )


class TestSignalEmittedSchema:
    """The event declares its schema version and is immutable."""

    def test_schema_version_is_one(self) -> None:
        """SignalEmitted pins schema version 1 for its first release."""
        assert SignalEmitted.__event_schema_version__ == 1

    def test_event_is_frozen(self) -> None:
        """Mutating a field of a published event raises."""
        event = _event()
        with pytest.raises((AttributeError, TypeError)):
            event.stop_price = Decimal("1")  # type: ignore[misc]


class TestFromSignal:
    """from_signal maps a StrategySignal onto the event without a clock."""

    def test_occurred_at_is_the_signal_time(self) -> None:
        """The event time is the bar close the signal carries, so replay is deterministic."""
        signal_time = datetime(2026, 3, 1, 8, 0, 0, tzinfo=UTC)
        event = _event(_entry_signal(signal_time=signal_time))
        assert event.occurred_at == signal_time

    def test_entry_signal_fields_are_copied(self) -> None:
        """Every entry-signal field lands on the event unchanged."""
        signal = _entry_signal(entry="50000", stop="48000", atr="1200")
        event = _event(signal)
        assert (event.kind, event.side) == (SignalKind.ENTRY, SignalSide.LONG)
        assert (event.entry_price, event.stop_price, event.atr_context) == (
            Decimal("50000"),
            Decimal("48000"),
            Decimal("1200"),
        )

    def test_exit_signal_keeps_its_reason_and_no_entry_price(self) -> None:
        """A pure exit keeps exit_reason and a None entry_price rather than inventing a price."""
        signal = StrategySignal(
            kind=SignalKind.EXIT,
            side=SignalSide.LONG,
            entry_price=None,
            stop_price=Decimal("48000"),
            atr_context=Decimal("1200"),
            signal_time=_BAR_CLOSE,
            exit_reason="chandelier",
        )
        event = _event(signal)
        assert (event.kind, event.entry_price, event.exit_reason) == (
            SignalKind.EXIT,
            None,
            "chandelier",
        )

    def test_f_star_estimate_is_copied(self) -> None:
        """The Kelly estimate is carried through, not reset to None."""
        signal = dataclasses.replace(_entry_signal(), f_star_estimate=Decimal("0.05"))
        assert _event(signal).f_star_estimate == Decimal("0.05")

    def test_strategy_id_pair_and_correlation_come_from_the_caller(self) -> None:
        """Strategy id, pair and correlation id are the caller's, since the signal has none."""
        event = _event()
        assert (event.strategy_id, event.pair, event.correlation_id) == (
            _STRATEGY,
            _PAIR,
            _CORRELATION,
        )

    def test_event_seq_defaults_to_zero(self) -> None:
        """A freshly built event starts its correlation at sequence 0."""
        assert _event().event_seq == 0


class TestSignalEmittedDriftGuard:
    """The event cannot silently lag the signal's shape."""

    def test_every_signal_field_except_signal_time_exists_on_the_event(self) -> None:
        """A field added to StrategySignal without an event field fails here, not in production."""
        signal_fields = {f.name for f in dataclasses.fields(StrategySignal)}
        event_fields = {f.name for f in dataclasses.fields(SignalEmitted)}
        assert signal_fields - {"signal_time"} - event_fields == set()


class TestSignalEmittedValidation:
    """Invalid timestamps and sequence numbers are refused at construction."""

    def test_naive_occurred_at_is_rejected(self) -> None:
        """A naive occurred_at raises ValueError naming the UTC requirement."""
        with pytest.raises(ValueError, match="UTC-aware"):
            SignalEmitted(
                occurred_at=datetime(2026, 10, 2, 16, 0, 0),
                correlation_id=_CORRELATION,
                strategy_id=_STRATEGY,
                pair=_PAIR,
                kind=SignalKind.ENTRY,
                side=SignalSide.LONG,
                entry_price=Decimal("50000"),
                stop_price=Decimal("48000"),
                atr_context=Decimal("1200"),
            )

    def test_negative_event_seq_is_rejected(self) -> None:
        """The base-class event_seq check still runs, because __post_init__ calls it."""
        with pytest.raises(ValueError, match="event_seq"):
            SignalEmitted(
                occurred_at=_BAR_CLOSE,
                correlation_id=_CORRELATION,
                event_seq=-1,
                strategy_id=_STRATEGY,
                pair=_PAIR,
                kind=SignalKind.ENTRY,
                side=SignalSide.LONG,
                entry_price=Decimal("50000"),
                stop_price=Decimal("48000"),
                atr_context=Decimal("1200"),
            )

    def test_non_utc_aware_offset_is_accepted_as_aware(self) -> None:
        """An aware datetime in another offset is not rejected: only naive ones are."""
        event = SignalEmitted.from_signal(
            _entry_signal(
                signal_time=datetime(2026, 10, 2, 18, 0, 0, tzinfo=timezone(timedelta(hours=2)))
            ),
            strategy_id=_STRATEGY,
            pair=_PAIR,
            correlation_id=_CORRELATION,
        )
        assert event.occurred_at == _BAR_CLOSE


class TestSignalEmittedPayloadHash:
    """payload_sha256 is computed (BalanceLow skips it) and deterministic."""

    def test_payload_sha256_is_a_64_char_hex_digest(self) -> None:
        """The hash is a BLAKE2b-256 hex digest, not the empty string a skipped base call leaves."""
        assert re.fullmatch(r"[0-9a-f]{64}", _event().payload_sha256)

    def test_changing_a_field_changes_the_hash(self) -> None:
        """The hash covers the signal fields: a different stop gives a different digest."""
        assert _event().payload_sha256 != _event(_entry_signal(stop="47000")).payload_sha256

    def test_changing_the_strategy_id_changes_the_hash(self) -> None:
        """The hash covers the strategy id the signal came from."""
        other = SignalEmitted.from_signal(
            _entry_signal(),
            strategy_id=StrategyId("swing-donchian-1d"),
            pair=_PAIR,
            correlation_id=_CORRELATION,
        )
        assert other.payload_sha256 != _event().payload_sha256

    @settings(max_examples=60)
    @given(
        entry=st.decimals(
            min_value=Decimal("10"), max_value=Decimal("200000"), places=2, allow_nan=False
        ),
        gap=st.decimals(
            min_value=Decimal("0.01"), max_value=Decimal("9"), places=2, allow_nan=False
        ),
        atr=st.decimals(
            min_value=Decimal("0.01"), max_value=Decimal("5000"), places=2, allow_nan=False
        ),
    )
    def test_equal_inputs_give_equal_hashes(
        self, entry: Decimal, gap: Decimal, atr: Decimal
    ) -> None:
        """Events built from equal signals and ids have equal payload hashes and equal values."""
        signal = StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=entry,
            stop_price=entry - gap,
            atr_context=atr,
            signal_time=_BAR_CLOSE,
        )
        first, second = _event(signal), _event(signal)
        assert first.payload_sha256 == second.payload_sha256
        assert first == second
