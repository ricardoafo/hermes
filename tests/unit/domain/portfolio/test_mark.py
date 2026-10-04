"""Unit tests for :class:`PortfolioMark` (#387).

Invariant under test: a mark's bar close time is timezone-aware, so stored
marks sort and compare correctly against UTC bar times.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_SEED = PortfolioSnapshot.seed(
    StrategyId("swing"), Money(amount=Decimal(5000), currency=Currency.USDT)
)


def test_aware_close_time_is_kept() -> None:
    """A UTC close time is stored as given."""
    close = datetime(2026, 10, 3, 15, 59, 59, tzinfo=UTC)
    mark = PortfolioMark(snapshot=_SEED, pair=_BTC, interval=KlineInterval.H4, bar_close_time=close)
    assert (mark.bar_close_time, mark.snapshot.cash) == (close, Decimal(5000))


def test_naive_close_time_is_rejected() -> None:
    """A naive close time would sort wrongly against stored UTC rows."""
    with pytest.raises(ValueError, match="timezone-aware"):
        PortfolioMark(
            snapshot=_SEED,
            pair=_BTC,
            interval=KlineInterval.H4,
            bar_close_time=datetime(2026, 10, 3, 12),
        )
