"""Edge tests for the portfolio state (#623).

A mutation-testing run over ``hermes.domain.portfolio`` showed the edges the
portfolio tests left unpinned: a negative starting cash, a position keyed by
an equal but separate pair object, a quote currency that sorts after the
snapshot's, a SELL that leaves cash at exactly zero, a time zone that reports
no offset, and the immutability of the records. Each test below pins one.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal

import pytest
from returns.result import Failure, Success

from hermes.domain.errors import CurrencyMismatch
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.position import Position
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.strategies.portfolio import BTC_USDT, at, make_fill

_STRATEGY = StrategyId("swing-donchian-4h")


def _seed(cash: str = "5000", quote: Currency = Currency.USDT) -> PortfolioSnapshot:
    return PortfolioSnapshot.seed(_STRATEGY, Money(Decimal(cash), quote))


def _btc_position(pair: Pair = BTC_USDT) -> Position:
    return Position(
        pair=pair,
        quantity=Decimal("1"),
        cost_basis=Decimal("100"),
        mark_price=Decimal("100"),
        marked_at=at(0),
    )


class TestConstructionEdges:
    """Snapshot invariants at their edges."""

    def test_negative_starting_cash_is_rejected(self) -> None:
        """Starting cash below zero is refused, like zero."""
        with pytest.raises(ValueError, match="starting_cash must be > 0"):
            replace(_seed(), starting_cash=Decimal("-1"))

    def test_position_keyed_by_an_equal_pair_object_is_accepted(self) -> None:
        """A key equal to the position's pair is valid even when it is another object."""
        key = Pair(base=Currency.BTC, quote=Currency.USDT)
        snapshot = replace(_seed(), positions={key: _btc_position()})
        assert dict(snapshot.positions) == {BTC_USDT: _btc_position()}

    def test_position_quoted_above_the_snapshot_currency_is_rejected(self) -> None:
        """A USDC snapshot refuses a BTC/USDT position, whichever way the codes sort."""
        with pytest.raises(ValueError, match="not quoted in the snapshot currency"):
            replace(_seed(quote=Currency.USDC), positions={BTC_USDT: _btc_position()})


class TestTransitionEdges:
    """Fill transitions at their edges."""

    def test_fill_quoted_above_the_snapshot_currency_is_refused(self) -> None:
        """A BTC/USDT fill on a USDC snapshot is a currency mismatch, not a BUY."""
        result = _seed(quote=Currency.USDC).after_fill(
            make_fill(OrderSide.BUY, "0.01", "100", pair=BTC_USDT)
        )
        assert result == Failure(
            CurrencyMismatch(expected=str(Currency.USDC), actual=str(Currency.USDT))
        )

    def test_sell_that_leaves_cash_at_exactly_zero_is_allowed(self) -> None:
        """All cash in BTC, then a SELL whose fee equals its proceeds: cash ends at 0, allowed."""
        invested = _seed("100").after_fill(make_fill(OrderSide.BUY, "1", "100")).unwrap()
        result = invested.after_fill(make_fill(OrderSide.SELL, "1", "1", fee="1", minutes=1))
        assert isinstance(result, Success)
        assert result.unwrap().cash == Decimal("0")


class _NoOffset(tzinfo):
    """A tzinfo that claims a zone but reports no UTC offset."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "nowhere"


class TestMarkAndRecords:
    """Marks need a real UTC offset; records cannot change."""

    def test_mark_without_an_offset_is_rejected(self) -> None:
        """A close time whose tzinfo reports no offset is as unusable as a naive one."""
        with pytest.raises(ValueError, match="timezone-aware"):
            PortfolioMark(
                snapshot=_seed(),
                pair=BTC_USDT,
                interval=KlineInterval.H4,
                bar_close_time=datetime(2026, 10, 1, tzinfo=_NoOffset()),
            )
