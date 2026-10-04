"""Tests for the FillQualityRecorded domain event.

Invariants protected here: the row the SHADOW_CERTIFIED gate reads can never
hold a drift without the model price it was measured against (or a slippage
without its mid), never a naive time, and never a non-positive fill.

Reference: openspec/changes/353-fill-kernel-in-serve/change.md D-4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from hermes.domain.events.fill_quality_recorded import BookSource, FillQualityRecorded
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
