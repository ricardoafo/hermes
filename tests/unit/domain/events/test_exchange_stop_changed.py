"""Unit tests for :class:`ExchangeStopChanged` (#1080).

Rules: the ledger row of a stop that rests always names its order, quantity
and price, so a restart can find it on the exchange; its reason is a fixed
snake_case word, never free text that could carry a figure (ADR-012 D-02).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged, StopAction
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

AT = datetime(2024, 10, 29, 4, 0, 5, tzinfo=UTC)
ENTRY = datetime(2024, 10, 29, 3, 59, 59, 999000, tzinfo=UTC)


def _row(
    action: StopAction,
    *,
    order_id: str | None = "BTCUSDT:1000",
    reason: str | None = "protect_position",
    entry_time: datetime | None = ENTRY,
) -> ExchangeStopChanged:
    return ExchangeStopChanged(
        occurred_at=AT,
        correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        action=action,
        entry_time=entry_time,
        order_id=order_id,
        qty=Decimal("0.5"),
        stop_price=Decimal(140),
        reason=reason,
    )


@pytest.mark.parametrize(
    ("action", "rests"),
    [
        (StopAction.PLACED, True),
        (StopAction.ADOPTED, True),
        (StopAction.CANCELLED, False),
        (StopAction.FILLED, False),
        (StopAction.GONE, False),
        (StopAction.FAILED, False),
    ],
)
def test_only_a_placed_or_adopted_stop_rests(action: StopAction, rests: bool) -> None:
    """The restart looks a stop up on the exchange only when its row says it rests."""
    assert _row(action).rests is rests


@pytest.mark.parametrize("action", [StopAction.PLACED, StopAction.ADOPTED])
def test_a_resting_stop_without_its_order_id_is_refused(action: StopAction) -> None:
    """A resting row with no order id could never be found or cancelled again."""
    with pytest.raises(ValueError, match="needs its order id"):
        _row(action, order_id=None)


def test_a_failed_row_needs_no_order_id() -> None:
    """A failed placement has no order; its row records the failure all the same."""
    row = _row(StopAction.FAILED, order_id=None, reason="refused")
    assert (row.action, row.order_id, row.reason) == (StopAction.FAILED, None, "refused")


@pytest.mark.parametrize("reason", ["stop moved to 142", "Refused", "a-b"])
def test_a_free_text_reason_is_refused(reason: str) -> None:
    """Only fixed snake_case reasons, so no price or quantity reaches the row's reason."""
    with pytest.raises(ValueError, match="snake_case"):
        _row(StopAction.CANCELLED, reason=reason)


def test_a_naive_entry_time_is_refused() -> None:
    """The entry time keys the position like a stop mark, in UTC."""
    with pytest.raises(ValueError, match="entry_time"):
        _row(StopAction.PLACED, entry_time=datetime(2024, 10, 29, 4))
