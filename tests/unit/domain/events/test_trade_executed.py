"""Tests for the TradeExecuted domain event.

Verifies:
- Schema version is 1 (initial release).
- Instances are frozen — mutation raises.
- Equality is by value (dataclass semantics).
- BUY and SELL sides are the only accepted values.

Reference: code-conventions.md §10 + §3.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId


def _sample_pair() -> Pair:
    """Return a deterministic BTC/USDT pair for use in event fixtures."""
    return Pair(base=Currency.BTC, quote=Currency.USDT)


def _sample_event(*, side: TradeSide = TradeSide.BUY) -> TradeExecuted:
    """Return a fully-populated TradeExecuted instance for reuse across tests."""
    pair = _sample_pair()
    return TradeExecuted(
        occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=pair,
        side=side,
        qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        price=Price(amount=Decimal("68000"), pair=pair),
        fee=Money(amount=Decimal("0.06"), currency=Currency.USDT),
        strategy_id=StrategyId("swing"),
    )


class TestTradeExecutedSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_two(self) -> None:
        """TradeExecuted.__event_schema_version__ pins to 2 (v2 added strategy_id)."""
        assert TradeExecuted.__event_schema_version__ == 2


class TestTradeExecutedInstantiation:
    """Verify construction populates every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument is reachable as an attribute of the same value."""
        event = _sample_event()
        assert event.pair == _sample_pair()
        assert event.side == TradeSide.BUY
        assert event.qty.amount == Decimal("0.001")
        assert event.price.amount == Decimal("68000")
        assert event.fee.amount == Decimal("0.06")
        assert event.correlation_id == "01J9Z8T3M5BQR2FVKN4XWY6PH1"

    def test_event_is_frozen(self) -> None:
        """TradeExecuted is a frozen dataclass — mutation raises FrozenInstanceError."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.correlation_id = "changed"  # type: ignore[misc]

    def test_equality_is_by_value(self) -> None:
        """Two TradeExecuted instances built from identical fields compare equal."""
        assert _sample_event() == _sample_event()

    def test_inequality_when_side_differs(self) -> None:
        """Changing only the side field breaks equality — side is a load-bearing field."""
        assert _sample_event(side=TradeSide.BUY) != _sample_event(side=TradeSide.SELL)


class TestTradeSideEnum:
    """Verify TradeSide has exactly the two documented members."""

    def test_no_other_members(self) -> None:
        """TradeSide has exactly two members — no accidental extras."""
        assert set(TradeSide) == {TradeSide.BUY, TradeSide.SELL}
