"""Tests for the TradeRejected domain event.

Verifies:
- Schema version is 1.
- Frozen semantics.
- Every field round-trips.
- Accepts either TradeSide.

Reference: code-conventions.md §10 + §3.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair


def _sample_event(*, side: TradeSide = TradeSide.BUY) -> TradeRejected:
    """Return a deterministic TradeRejected instance for reuse across tests."""
    return TradeRejected(
        occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        side=side,
        rejection_reason="insufficient balance",
        exchange="binance",
        attempted_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
    )


class TestTradeRejectedSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_one(self) -> None:
        """TradeRejected.__event_schema_version__ pins to 1 for the initial release."""
        assert TradeRejected.__event_schema_version__ == 1


class TestTradeRejectedInstantiation:
    """Verify construction populates every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument reaches the instance with the value passed."""
        event = _sample_event()
        assert event.pair == Pair(base=Currency.BTC, quote=Currency.USDT)
        assert event.side == TradeSide.BUY
        assert event.rejection_reason == "insufficient balance"
        assert event.exchange == "binance"
        assert event.attempted_at == datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)

    def test_event_is_frozen(self) -> None:
        """TradeRejected is a frozen dataclass — mutation raises."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.exchange = "kraken"  # type: ignore[misc]

    def test_equality_is_by_value(self) -> None:
        """Two TradeRejected instances with identical fields compare equal."""
        assert _sample_event() == _sample_event()


class TestTradeRejectedSide:
    """Both TradeSide members are accepted rejection sides."""

    @pytest.mark.parametrize("side", list(TradeSide))
    def test_accepts_every_side_member(self, side: TradeSide) -> None:
        """Constructor accepts both BUY and SELL sides without raising."""
        event = _sample_event(side=side)
        assert event.side == side
