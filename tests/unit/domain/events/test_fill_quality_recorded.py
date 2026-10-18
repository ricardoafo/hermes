"""Tests for the FillQualityRecorded domain event.

Invariants protected here: the row the SHADOW_CERTIFIED gate reads can never
hold a drift without the model price it was measured against (or a slippage
without its mid), never a naive time, and never a non-positive fill.

Reference: openspec/archive/353-fill-kernel-in-serve/change.md D-4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from hermes.domain.events.fill_quality_recorded import (
    BookSource,
    FillQualityRecorded,
    upcast_fill_quality_payload,
)
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_AT = datetime(2026, 10, 4, 4, 0, 5, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"


def _event(**overrides: Any) -> FillQualityRecorded:
    """Return a valid live-book event with ``overrides`` applied."""
    fields: dict[str, Any] = {
        "occurred_at": _AT,
        "correlation_id": _CID,
        "strategy_id": StrategyId("swing-donchian-4h"),
        "pair": Pair(base=Currency.BTC, quote=Currency.USDT),
        "side": TradeSide.BUY,
        "qty": Decimal("0.001"),
        "book_source": BookSource.LIVE,
        "book_time": _AT,
        "fill_price": Decimal("84833.12"),
        "model_price": Decimal("84830"),
        "drift_bps": Decimal("0.37"),
        "book_mid": Decimal("84833.115"),
        "slippage": Decimal("0.0000001"),
    }
    fields.update(overrides)
    return FillQualityRecorded(**fields)


def test_a_complete_event_keeps_its_drift() -> None:
    """A valid row stores the drift it was given."""
    assert _event().drift_bps == Decimal("0.37")


def test_a_failed_model_walk_stores_neither_price_nor_drift() -> None:
    """``model_price=None`` with ``drift_bps=None`` is the recorded "no model" case."""
    event = _event(model_price=None, drift_bps=None)
    assert (event.model_price, event.drift_bps) == (None, None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_price": None},
        {"drift_bps": None},
        {"book_mid": None},
        {"slippage": None},
    ],
)
def test_a_measure_without_its_reference_is_refused(overrides: dict[str, Any]) -> None:
    """Half of a (reference, measure) pair is never stored."""
    with pytest.raises(ValueError, match="needs"):
        _event(**overrides)


def test_a_naive_book_time_is_refused() -> None:
    """Book times are UTC-aware like every ledger time."""
    naive = datetime(2026, 10, 4)
    with pytest.raises(ValueError, match="book_time must be UTC-aware"):
        _event(book_time=naive)


def test_a_zero_quantity_is_refused() -> None:
    """A fill of nothing is not a fill."""
    with pytest.raises(ValueError, match="qty and fill_price > 0"):
        _event(qty=Decimal("0"))


def test_arrival_price_and_delta_default_to_none() -> None:
    """Constructors written before schema v2 still build, with the new fields unset."""
    event = _event()
    assert (event.arrival_price, event.delta_ms) == (None, None)


def test_arrival_price_and_delta_are_kept_when_given() -> None:
    """The decision price and latency the executor passes are stored unchanged."""
    event = _event(arrival_price=Decimal("85000"), delta_ms=6000)
    assert (event.arrival_price, event.delta_ms) == (Decimal("85000"), 6000)


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1")])
def test_a_non_positive_arrival_price_is_refused(price: Decimal) -> None:
    """A decision price of zero or less cannot be a market price."""
    with pytest.raises(ValueError, match="arrival_price must be > 0"):
        _event(arrival_price=price)


def test_a_negative_delta_is_refused() -> None:
    """A fill cannot precede the decision that caused it."""
    with pytest.raises(ValueError, match="delta_ms must be >= 0"):
        _event(delta_ms=-1)


def test_a_zero_delta_is_accepted() -> None:
    """A fill stamped at the signal time has zero latency, not an error."""
    assert _event(delta_ms=0).delta_ms == 0


def test_event_is_at_schema_version_two() -> None:
    """Adding fields bumps the schema version (code-conventions §10)."""
    assert FillQualityRecorded.__event_schema_version__ == 2


def test_a_v1_payload_upcasts_with_the_new_fields_none() -> None:
    """Rows written before v2 read back with arrival_price and delta_ms as None."""
    v1 = {"fill_price": "84833.12"}
    assert upcast_fill_quality_payload(1, v1) == {
        "fill_price": "84833.12",
        "arrival_price": None,
        "delta_ms": None,
    }


def test_a_v2_payload_is_not_changed_by_the_upcaster() -> None:
    """Current rows pass through untouched."""
    v2 = {"arrival_price": "85000", "delta_ms": 6000}
    assert upcast_fill_quality_payload(2, v2) == {"arrival_price": "85000", "delta_ms": 6000}


def test_an_unknown_payload_version_is_refused() -> None:
    """A row from a future schema is an error, not a silent partial read."""
    with pytest.raises(ValueError, match="unsupported FillQualityRecorded schema version 3"):
        upcast_fill_quality_payload(3, {})
