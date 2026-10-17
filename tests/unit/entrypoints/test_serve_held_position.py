"""Unit tests for when serve says a held coin was bought (issue #902).

Invariants under test:

- The entry is the first buy since the pair was last flat, not the latest buy
  and not a buy from an earlier round trip.
- A coin sold back to flat has no entry.
- The entry is the close time of the 4h bar the fill landed after, which is
  what the strategy keys its time stop and highest high on.
- Trades after ``at_or_before`` and trades of other pairs do not count.
"""

from datetime import UTC, datetime, timedelta

from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.entrypoints.serve import _holding_opened_at
from tests.strategies.portfolio import BTC_USDT, ETH_USDT, make_fill

_FILLED_AT = datetime(2024, 10, 29, 4, 0, 5, tzinfo=UTC)
_BAR_CLOSE = datetime(2024, 10, 29, 3, 59, 59, 999000, tzinfo=UTC)
_LATER = _FILLED_AT + timedelta(days=30)


def _trade(
    side: OrderSide, at: datetime, *, pair: Pair = BTC_USDT, qty: str = "1"
) -> TradeExecuted:
    fill = make_fill(side, qty, "100", pair=pair)
    return TradeExecuted(
        occurred_at=at,
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=fill.order.pair,
        side=TradeSide(side.value),
        qty=fill.order.qty,
        price=Price(amount=fill.avg_price, pair=fill.order.pair),
        fee=fill.fee,
        strategy_id=fill.strategy_id,
    )


def test_the_entry_is_the_close_of_the_bar_the_buy_filled_after() -> None:
    """A fill 6 s after the 03:59:59.999 close enters on that bar, not at the fill time."""
    trades = [_trade(OrderSide.BUY, _FILLED_AT)]
    assert _holding_opened_at(trades, BTC_USDT, _LATER) == _BAR_CLOSE


def test_a_fill_exactly_at_a_bar_close_enters_on_that_bar() -> None:
    """A fill stamped on the close time itself keeps that close time."""
    trades = [_trade(OrderSide.BUY, _BAR_CLOSE)]
    assert _holding_opened_at(trades, BTC_USDT, _LATER) == _BAR_CLOSE


def test_a_second_buy_keeps_the_first_buys_entry() -> None:
    """Adding to a held coin does not restart its time stop."""
    trades = [
        _trade(OrderSide.BUY, _FILLED_AT),
        _trade(OrderSide.BUY, _FILLED_AT + timedelta(days=3)),
    ]
    assert _holding_opened_at(trades, BTC_USDT, _LATER) == _BAR_CLOSE


def test_a_buy_after_a_round_trip_is_the_new_entry() -> None:
    """A coin sold to flat and bought again enters on the new buy's bar."""
    rebought = _FILLED_AT + timedelta(days=10)
    trades = [
        _trade(OrderSide.BUY, _FILLED_AT),
        _trade(OrderSide.SELL, _FILLED_AT + timedelta(days=5)),
        _trade(OrderSide.BUY, rebought),
    ]
    assert _holding_opened_at(trades, BTC_USDT, _LATER) == _BAR_CLOSE + timedelta(days=10)


def test_a_coin_sold_back_to_flat_has_no_entry() -> None:
    """A partial sell keeps the entry; selling the rest leaves none."""
    partial = [
        _trade(OrderSide.BUY, _FILLED_AT, qty="2"),
        _trade(OrderSide.SELL, _FILLED_AT, qty="1"),
    ]
    flat = [*partial, _trade(OrderSide.SELL, _FILLED_AT + timedelta(hours=4), qty="1")]
    assert (
        _holding_opened_at(partial, BTC_USDT, _LATER),
        _holding_opened_at(flat, BTC_USDT, _LATER),
    ) == (_BAR_CLOSE, None)


def test_trades_after_the_cutoff_or_of_other_pairs_do_not_count() -> None:
    """Only this pair's trades up to ``at_or_before`` decide the entry."""
    trades = [
        _trade(OrderSide.BUY, _FILLED_AT, pair=ETH_USDT),
        _trade(OrderSide.BUY, _LATER + timedelta(hours=1)),
    ]
    after_the_buy = _LATER + timedelta(hours=2)
    assert (
        _holding_opened_at(trades, BTC_USDT, _LATER),
        _holding_opened_at(trades, BTC_USDT, after_the_buy),
    ) == (None, datetime(2024, 11, 28, 7, 59, 59, 999000, tzinfo=UTC))
