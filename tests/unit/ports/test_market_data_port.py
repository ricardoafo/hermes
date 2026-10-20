"""Structural-conformance harness for :class:`MarketDataPort`.

Protects the following invariants:

1. The port declares exactly four public methods: ``stream_bars``, ``stream_trades``,
   ``stream_orderbook_diffs`` and ``get_snapshot``.
2. The reusable fake's methods have the port's parameter names, kinds and type hints.
3. ``stream_bars`` yields :class:`~hermes.domain.market_data.bar.Bar` instances that round-trip
   correctly.
4. ``stream_trades`` yields :class:`~hermes.domain.market_data.trade.Trade` instances that
   round-trip correctly.
5. ``stream_orderbook_diffs`` yields
   :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff` instances with the
   correct sequence invariant.
6. ``get_snapshot`` returns a valid :class:`~hermes.domain.market_data.orderbook.Orderbook`.
7. :class:`~hermes.domain.market_data.bar.Bar` rejects naive datetime timestamps.
8. :class:`~hermes.domain.market_data.bar.Bar` rejects non-positive prices and negative volume.
9. :class:`~hermes.domain.market_data.bar.Bar` rejects a close_time <= open_time.
10. :class:`~hermes.domain.market_data.bar.Bar` rejects crossed high/low/open/close.
11. :class:`~hermes.domain.market_data.trade.Trade` rejects naive datetime timestamps.
12. :class:`~hermes.domain.market_data.trade.Trade` rejects non-positive price or quantity.
13. :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiffLevel` rejects
    non-positive price.
14. :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiffLevel` rejects
    negative quantity.
15. :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff` rejects
    ``final_update_id < first_update_id``.

This module provides a reusable ``FakeMarketDataAdapter`` that any adapter author can
import to validate their own implementation satisfies the Protocol contract.
"""

import asyncio
import inspect
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import get_type_hints

import pytest

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.market_data.orderbook_diff import OrderbookDiff, OrderbookDiffLevel
from hermes.domain.market_data.trade import Trade, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.market_data import MarketDataPort

# ────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ────────────────────────────────────────────────────────────────

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
_ONE_MINUTE_LATER = _NOW + timedelta(minutes=1)


def _level(price: str, qty: str = "1") -> OrderbookLevel:
    """Build an :class:`OrderbookLevel` from string decimals."""
    return OrderbookLevel(price=Decimal(price), quantity=Decimal(qty))


def _diff_level(price: str, qty: str = "1", *, delete: bool = False) -> OrderbookDiffLevel:
    """Build an :class:`OrderbookDiffLevel` from string decimals."""
    return OrderbookDiffLevel(price=Decimal(price), quantity=Decimal(qty), delete=delete)


def _make_bar() -> Bar:
    """Return a valid :class:`Bar` for BTC/USDT M1 at _NOW."""
    return Bar(
        pair=_BTC_USDT,
        interval=KlineInterval.M1,
        open_time=_NOW,
        open=Decimal("50000"),
        high=Decimal("50500"),
        low=Decimal("49900"),
        close=Decimal("50300"),
        volume=Decimal("1.5"),
        close_time=_ONE_MINUTE_LATER,
    )


def _make_trade() -> Trade:
    """Return a valid :class:`Trade` for BTC/USDT."""
    return Trade(
        pair=_BTC_USDT,
        price=Decimal("50300"),
        quantity=Decimal("0.1"),
        side=TradeSide.BUY,
        trade_time=_NOW,
        trade_id="123456",
    )


def _make_diff() -> OrderbookDiff:
    """Return a valid :class:`OrderbookDiff` for BTC/USDT."""
    return OrderbookDiff(
        pair=_BTC_USDT,
        bids=(_diff_level("50000", "2"),),
        asks=(_diff_level("50100", "1"),),
        first_update_id=1000,
        final_update_id=1001,
    )


def _make_snapshot() -> Orderbook:
    """Return a valid :class:`Orderbook` snapshot for BTC/USDT."""
    return Orderbook(
        pair=_BTC_USDT,
        bids=(_level("50000"),),
        asks=(_level("50100"),),
        snapshot_time=_NOW,
    )


class FakeMarketDataAdapter:
    """In-memory adapter for Protocol conformance verification.

    Yields one pre-seeded item from each stream method, then exhausts.
    This implementation is importable by adapter authors as a baseline
    conformance check before wiring up real exchange connections.
    """

    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        """Yield one synthetic bar, then exhaust."""

        async def _gen() -> AsyncIterator[Bar]:
            yield _make_bar()

        return _gen()

    async def stream_trades(self, pair: Pair) -> AsyncIterator[Trade]:
        """Yield one synthetic trade, then exhaust."""

        async def _gen() -> AsyncIterator[Trade]:
            yield _make_trade()

        return _gen()

    async def stream_orderbook_diffs(self, pair: Pair) -> AsyncIterator[OrderbookDiff]:
        """Yield one synthetic depth-update event, then exhaust."""

        async def _gen() -> AsyncIterator[OrderbookDiff]:
            yield _make_diff()

        return _gen()

    async def get_snapshot(self, pair: Pair) -> Orderbook:
        """Return a synthetic depth snapshot."""
        return _make_snapshot()


_PORT_METHODS = ["stream_bars", "stream_trades", "stream_orderbook_diffs", "get_snapshot"]


def _public_methods(cls: type) -> list[str]:
    """Return the sorted names of ``cls``'s public callables."""
    return sorted(
        name for name, member in vars(cls).items() if not name.startswith("_") and callable(member)
    )


# ────────────────────────────────────────────────────────────────
# Protocol conformance — isinstance checks
# ────────────────────────────────────────────────────────────────


class TestMarketDataPortConformance:
    """Protocol implementability: ``isinstance`` guards match structurally."""

    def test_port_declares_exactly_the_four_stream_and_snapshot_methods(self) -> None:
        """The port's public surface is the four documented methods, no more."""
        assert _public_methods(MarketDataPort) == [
            "get_snapshot",
            "stream_bars",
            "stream_orderbook_diffs",
            "stream_trades",
        ]

    @pytest.mark.parametrize("method", _PORT_METHODS)
    def test_fake_adapter_method_matches_the_port_signature(self, method: str) -> None:
        """Each fake method has the port's parameter names, kinds and type hints."""
        fake = getattr(FakeMarketDataAdapter, method)
        port = getattr(MarketDataPort, method)

        assert [(p.name, p.kind) for p in inspect.signature(fake).parameters.values()] == [
            (p.name, p.kind) for p in inspect.signature(port).parameters.values()
        ]
        assert get_type_hints(fake) == get_type_hints(port)


# ────────────────────────────────────────────────────────────────
# Stream output correctness
# ────────────────────────────────────────────────────────────────


class TestFakeAdapterStreamOutputs:
    """Streams yield domain objects with correct field values."""

    def test_stream_bars_yields_bar_with_correct_pair(self) -> None:
        """stream_bars yields a Bar whose pair matches the subscription pair."""
        adapter = FakeMarketDataAdapter()

        async def _run() -> Bar:
            it = await adapter.stream_bars(_BTC_USDT, KlineInterval.M1)
            return await it.__anext__()

        bar = asyncio.run(_run())
        assert bar.pair == _BTC_USDT, (
            f"Bar.pair must equal the subscribed pair, got {bar.pair!r}, expected {_BTC_USDT!r}"
        )

    def test_stream_bars_yields_bar_with_correct_interval(self) -> None:
        """stream_bars yields a Bar with the requested KlineInterval."""
        adapter = FakeMarketDataAdapter()

        async def _run() -> Bar:
            it = await adapter.stream_bars(_BTC_USDT, KlineInterval.M1)
            return await it.__anext__()

        bar = asyncio.run(_run())
        assert bar.interval == KlineInterval.M1, (
            f"Bar.interval must equal the requested interval M1, got {bar.interval!r}"
        )

    def test_stream_bars_yields_bar_with_utc_open_time(self) -> None:
        """stream_bars yields a Bar whose open_time is UTC-aware."""
        adapter = FakeMarketDataAdapter()

        async def _run() -> Bar:
            it = await adapter.stream_bars(_BTC_USDT, KlineInterval.M1)
            return await it.__anext__()

        bar = asyncio.run(_run())
        assert bar.open_time.tzinfo is not None, (
            "Bar.open_time must be timezone-aware (UTC) — "
            "naive datetimes are rejected by the domain"
        )
        assert bar.open_time == _NOW, f"Bar.open_time must equal _NOW, got {bar.open_time!r}"

    def test_stream_trades_yields_trade_with_correct_side(self) -> None:
        """stream_trades yields a Trade with the correct aggressor side."""
        adapter = FakeMarketDataAdapter()

        async def _run() -> Trade:
            it = await adapter.stream_trades(_BTC_USDT)
            return await it.__anext__()

        trade = asyncio.run(_run())
        assert trade.side == TradeSide.BUY, (
            f"Trade.side must equal TradeSide.BUY, got {trade.side!r}"
        )

    def test_stream_trades_yields_trade_with_positive_price(self) -> None:
        """stream_trades yields a Trade with a positive execution price."""
        adapter = FakeMarketDataAdapter()

        async def _run() -> Trade:
            it = await adapter.stream_trades(_BTC_USDT)
            return await it.__anext__()

        trade = asyncio.run(_run())
        assert trade.price == Decimal("50300"), (
            f"Trade.price must equal Decimal('50300'), got {trade.price!r}"
        )

    def test_stream_orderbook_diffs_yields_diff_with_correct_sequence(self) -> None:
        """stream_orderbook_diffs yields an OrderbookDiff with final_update_id >= first."""
        adapter = FakeMarketDataAdapter()

        async def _run() -> OrderbookDiff:
            it = await adapter.stream_orderbook_diffs(_BTC_USDT)
            return await it.__anext__()

        diff = asyncio.run(_run())
        assert diff.final_update_id >= diff.first_update_id, (
            f"OrderbookDiff.final_update_id ({diff.final_update_id}) must be >= "
            f"first_update_id ({diff.first_update_id})"
        )

    def test_stream_orderbook_diffs_yields_diff_with_correct_pair(self) -> None:
        """stream_orderbook_diffs yields an OrderbookDiff for the subscribed pair."""
        adapter = FakeMarketDataAdapter()

        async def _run() -> OrderbookDiff:
            it = await adapter.stream_orderbook_diffs(_BTC_USDT)
            return await it.__anext__()

        diff = asyncio.run(_run())
        assert diff.pair == _BTC_USDT, (
            f"OrderbookDiff.pair must equal {_BTC_USDT!r}, got {diff.pair!r}"
        )

    def test_get_snapshot_returns_orderbook_for_pair(self) -> None:
        """get_snapshot returns an Orderbook whose pair matches the requested pair."""
        adapter = FakeMarketDataAdapter()
        snapshot = asyncio.run(adapter.get_snapshot(_BTC_USDT))
        assert snapshot.pair == _BTC_USDT, (
            f"Orderbook.pair must equal {_BTC_USDT!r}, got {snapshot.pair!r}"
        )


# ────────────────────────────────────────────────────────────────
# Bar invariants
# ────────────────────────────────────────────────────────────────


class TestBarInvariants:
    """Bar.__post_init__ enforces UTC timestamps, positive prices, and OHLC ordering."""

    def test_naive_open_time_raises(self) -> None:
        """Bar rejects naive open_time — UTC-aware timestamps are mandatory."""
        with pytest.raises(ValueError, match="open_time must be UTC-aware"):
            Bar(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=datetime(2026, 8, 25, 10, 0),  # naive
                open=Decimal("50000"),
                high=Decimal("50500"),
                low=Decimal("49900"),
                close=Decimal("50300"),
                volume=Decimal("1.5"),
                close_time=_ONE_MINUTE_LATER,
            )

    def test_naive_close_time_raises(self) -> None:
        """Bar rejects naive close_time — UTC-aware timestamps are mandatory."""
        with pytest.raises(ValueError, match="close_time must be UTC-aware"):
            Bar(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=_NOW,
                open=Decimal("50000"),
                high=Decimal("50500"),
                low=Decimal("49900"),
                close=Decimal("50300"),
                volume=Decimal("1.5"),
                close_time=datetime(2026, 8, 25, 10, 1),  # naive
            )

    def test_close_time_not_after_open_time_raises(self) -> None:
        """Bar rejects close_time <= open_time — candle window must be positive."""
        with pytest.raises(ValueError, match=r"close_time.*must be strictly after"):
            Bar(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=_NOW,
                open=Decimal("50000"),
                high=Decimal("50500"),
                low=Decimal("49900"),
                close=Decimal("50300"),
                volume=Decimal("1.5"),
                close_time=_NOW,  # equal — not strictly after
            )

    def test_zero_open_price_raises(self) -> None:
        """Bar rejects a zero open price — resting prices must be positive."""
        with pytest.raises(ValueError, match="open must be > 0"):
            Bar(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=_NOW,
                open=Decimal("0"),
                high=Decimal("50500"),
                low=Decimal("49900"),
                close=Decimal("50300"),
                volume=Decimal("1.5"),
                close_time=_ONE_MINUTE_LATER,
            )

    def test_negative_volume_raises(self) -> None:
        """Bar rejects negative volume — traded quantity must be >= 0."""
        with pytest.raises(ValueError, match="volume must be >= 0"):
            Bar(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=_NOW,
                open=Decimal("50000"),
                high=Decimal("50500"),
                low=Decimal("49900"),
                close=Decimal("50300"),
                volume=Decimal("-1"),
                close_time=_ONE_MINUTE_LATER,
            )

    def test_high_less_than_low_raises(self) -> None:
        """Bar rejects high < low — the candle wick ordering is violated."""
        with pytest.raises(ValueError, match=r"high.*must be >= Bar\.low"):
            Bar(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=_NOW,
                open=Decimal("50000"),
                high=Decimal("49800"),  # below low
                low=Decimal("49900"),
                close=Decimal("50300"),
                volume=Decimal("1.5"),
                close_time=_ONE_MINUTE_LATER,
            )

    def test_high_less_than_close_raises(self) -> None:
        """Bar rejects high < close — the close cannot exceed the candle's high."""
        with pytest.raises(ValueError, match="high must be >= open and >= close"):
            Bar(
                pair=_BTC_USDT,
                interval=KlineInterval.M1,
                open_time=_NOW,
                open=Decimal("50000"),
                high=Decimal("50200"),
                low=Decimal("49900"),
                close=Decimal("50500"),  # close above high
                volume=Decimal("1.5"),
                close_time=_ONE_MINUTE_LATER,
            )

    def test_valid_bar_constructs(self) -> None:
        """A well-formed Bar constructs without raising."""
        bar = _make_bar()
        assert bar.pair == _BTC_USDT, f"bar.pair must equal {_BTC_USDT!r}, got {bar.pair!r}"
        assert bar.close == Decimal("50300"), (
            f"bar.close must equal Decimal('50300'), got {bar.close!r}"
        )

    def test_zero_volume_bar_constructs(self) -> None:
        """A bar with zero volume (no trades in the window) is valid — e.g. thin-market candles."""
        bar = Bar(
            pair=_BTC_USDT,
            interval=KlineInterval.M1,
            open_time=_NOW,
            open=Decimal("50000"),
            high=Decimal("50000"),
            low=Decimal("50000"),
            close=Decimal("50000"),
            volume=Decimal("0"),
            close_time=_ONE_MINUTE_LATER,
        )
        assert bar.volume == Decimal("0"), (
            f"bar.volume must equal Decimal('0') for a thin-market candle, got {bar.volume!r}"
        )


# ────────────────────────────────────────────────────────────────
# Trade invariants
# ────────────────────────────────────────────────────────────────


class TestTradeInvariants:
    """Trade.__post_init__ enforces UTC timestamp and positive price/quantity."""

    def test_naive_trade_time_raises(self) -> None:
        """Trade rejects naive trade_time — UTC-aware timestamps are mandatory."""
        with pytest.raises(ValueError, match="trade_time must be UTC-aware"):
            Trade(
                pair=_BTC_USDT,
                price=Decimal("50300"),
                quantity=Decimal("0.1"),
                side=TradeSide.BUY,
                trade_time=datetime(2026, 8, 25, 10, 0),  # naive
                trade_id="abc",
            )

    def test_zero_price_raises(self) -> None:
        """Trade rejects zero price — execution at zero price is not a real trade."""
        with pytest.raises(ValueError, match="price must be > 0"):
            Trade(
                pair=_BTC_USDT,
                price=Decimal("0"),
                quantity=Decimal("0.1"),
                side=TradeSide.BUY,
                trade_time=_NOW,
                trade_id="abc",
            )

    def test_negative_price_raises(self) -> None:
        """Trade rejects negative price — a nonsensical execution price."""
        with pytest.raises(ValueError, match="price must be > 0"):
            Trade(
                pair=_BTC_USDT,
                price=Decimal("-1"),
                quantity=Decimal("0.1"),
                side=TradeSide.BUY,
                trade_time=_NOW,
                trade_id="abc",
            )

    def test_zero_quantity_raises(self) -> None:
        """Trade rejects zero quantity — a trade with no base-asset volume is invalid."""
        with pytest.raises(ValueError, match="quantity must be > 0"):
            Trade(
                pair=_BTC_USDT,
                price=Decimal("50300"),
                quantity=Decimal("0"),
                side=TradeSide.BUY,
                trade_time=_NOW,
                trade_id="abc",
            )

    def test_sell_side_constructs(self) -> None:
        """Trade with TradeSide.SELL (taker sell) constructs correctly."""
        trade = Trade(
            pair=_BTC_USDT,
            price=Decimal("49900"),
            quantity=Decimal("0.5"),
            side=TradeSide.SELL,
            trade_time=_NOW,
            trade_id="seller-99",
        )
        assert trade.side == TradeSide.SELL, (
            f"trade.side must equal TradeSide.SELL, got {trade.side!r}"
        )

    def test_valid_trade_constructs(self) -> None:
        """A well-formed Trade constructs without raising."""
        trade = _make_trade()
        assert trade.trade_id == "123456", (
            f"trade.trade_id must equal '123456', got {trade.trade_id!r}"
        )


# ────────────────────────────────────────────────────────────────
# OrderbookDiffLevel invariants
# ────────────────────────────────────────────────────────────────


class TestOrderbookDiffLevelInvariants:
    """OrderbookDiffLevel rejects non-positive price or negative quantity."""

    def test_zero_price_raises(self) -> None:
        """OrderbookDiffLevel rejects zero price — no real price level is zero."""
        with pytest.raises(ValueError, match="price must be > 0"):
            OrderbookDiffLevel(price=Decimal("0"), quantity=Decimal("1"), delete=False)

    def test_negative_price_raises(self) -> None:
        """OrderbookDiffLevel rejects negative price — nonsensical for an L2 level."""
        with pytest.raises(ValueError, match="price must be > 0"):
            OrderbookDiffLevel(price=Decimal("-100"), quantity=Decimal("1"), delete=False)

    def test_negative_quantity_raises(self) -> None:
        """OrderbookDiffLevel rejects negative quantity — only 0 (delete) or positive are valid."""
        with pytest.raises(ValueError, match="quantity must be >= 0"):
            OrderbookDiffLevel(price=Decimal("50000"), quantity=Decimal("-1"), delete=False)

    def test_zero_quantity_with_delete_flag_constructs(self) -> None:
        """OrderbookDiffLevel with delete=True and quantity=0 is valid — models wire deletion."""
        level = OrderbookDiffLevel(price=Decimal("50000"), quantity=Decimal("0"), delete=True)
        assert level.delete is True, (
            f"level.delete must be True for a deletion event, got {level.delete!r}"
        )
        assert level.quantity == Decimal("0"), (
            f"level.quantity must equal Decimal('0') for a deletion event, got {level.quantity!r}"
        )


# ────────────────────────────────────────────────────────────────
# OrderbookDiff invariants
# ────────────────────────────────────────────────────────────────


class TestOrderbookDiffInvariants:
    """OrderbookDiff enforces sequence number ordering."""

    def test_final_less_than_first_raises(self) -> None:
        """OrderbookDiff rejects reversed sequence IDs — gap detection relies on order."""
        with pytest.raises(ValueError, match=r"final_update_id.*must be >= first_update_id"):
            OrderbookDiff(
                pair=_BTC_USDT,
                bids=(),
                asks=(_diff_level("50100"),),
                first_update_id=1001,
                final_update_id=1000,  # reversed — invalid
            )

    def test_equal_sequence_ids_constructs(self) -> None:
        """OrderbookDiff with first == final (single-update event) is valid."""
        diff = OrderbookDiff(
            pair=_BTC_USDT,
            bids=(_diff_level("50000"),),
            asks=(),
            first_update_id=999,
            final_update_id=999,
        )
        assert diff.first_update_id == diff.final_update_id, (
            f"first_update_id must equal final_update_id for single-update events, "
            f"got {diff.first_update_id!r} and {diff.final_update_id!r}"
        )

    def test_valid_diff_with_both_sides_constructs(self) -> None:
        """OrderbookDiff with both bid and ask updates constructs without raising."""
        diff = _make_diff()
        assert diff.first_update_id == 1000, (
            f"diff.first_update_id must equal 1000, got {diff.first_update_id!r}"
        )
        assert diff.final_update_id == 1001, (
            f"diff.final_update_id must equal 1001, got {diff.final_update_id!r}"
        )

    def test_empty_bids_asks_diff_constructs(self) -> None:
        """OrderbookDiff with both sides empty is valid — heartbeat / no-op update."""
        diff = OrderbookDiff(
            pair=_BTC_USDT,
            bids=(),
            asks=(),
            first_update_id=500,
            final_update_id=500,
        )
        assert (diff.bids, diff.asks, diff.first_update_id, diff.final_update_id) == (
            (),
            (),
            500,
            500,
        )
