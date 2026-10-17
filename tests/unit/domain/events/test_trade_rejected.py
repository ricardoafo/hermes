"""Tests for the TradeRejected domain event.

Verifies:
- Schema version is 2 (v2 adds the refused signal's strategy, kind and rule, #708).
- Frozen semantics.
- Every field round-trips; the v2 fields default to None for exchange rejections.
- Accepts either TradeSide.
- The upcaster lifts v1 payloads and refuses unknown versions.

Reference: code-conventions.md §10 + §3.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.events.trade_rejected import TradeRejected, upcast_trade_rejected_payload
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId


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

    def test_schema_version_is_two(self) -> None:
        """Adding the refused-signal fields bumps the schema version (code-conventions §10)."""
        assert TradeRejected.__event_schema_version__ == 2


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


class TestTradeRejectedRefusedSignal:
    """The v2 fields that record a refused strategy signal (#708)."""

    def test_exchange_rejection_leaves_the_signal_fields_none(self) -> None:
        """An exchange rejection built with the v1 fields only has no signal fields."""
        event = _sample_event()
        assert (event.strategy_id, event.signal_kind, event.rule) == (None, None, None)

    def test_refused_signal_fields_are_stored(self) -> None:
        """Strategy, signal kind and rule id reach the instance with the values passed."""
        event = TradeRejected(
            occurred_at=datetime(2024, 1, 3, 3, 59, 59, tzinfo=UTC),
            correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
            pair=Pair(base=Currency.ETH, quote=Currency.USDT),
            side=TradeSide.BUY,
            rejection_reason="macro_volatility",
            exchange="binance",
            attempted_at=datetime(2024, 1, 3, 3, 59, 59, tzinfo=UTC),
            strategy_id=StrategyId("swing-donchian-4h"),
            signal_kind=SignalKind.ENTRY,
            rule="RO-07",
        )
        assert (event.strategy_id, event.signal_kind, event.rule) == (
            StrategyId("swing-donchian-4h"),
            SignalKind.ENTRY,
            "RO-07",
        )


def test_a_v1_payload_upcasts_with_the_signal_fields_none() -> None:
    """Rows written before v2 read back with strategy_id, signal_kind and rule as None."""
    v1 = {"rejection_reason": "insufficient balance"}
    assert upcast_trade_rejected_payload(1, v1) == {
        "rejection_reason": "insufficient balance",
        "strategy_id": None,
        "signal_kind": None,
        "rule": None,
    }


def test_a_v2_payload_is_not_changed_by_the_upcaster() -> None:
    """Current rows pass through untouched."""
    v2 = {"rule": "RO-07", "signal_kind": "entry"}
    assert upcast_trade_rejected_payload(2, v2) == {"rule": "RO-07", "signal_kind": "entry"}


def test_an_unknown_payload_version_is_refused() -> None:
    """A row from a future schema is an error, not a silent partial read."""
    with pytest.raises(ValueError, match="unsupported TradeRejected schema version 3"):
        upcast_trade_rejected_payload(3, {})
