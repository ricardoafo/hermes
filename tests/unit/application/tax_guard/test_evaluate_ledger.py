"""Tests for the ledger-backed recharacterization guard (#135).

Invariants under test:

- ``evaluate_ledger`` asks the trade source for exactly the rolling window
  ``[now - window_days, now]`` and judges what comes back.
- End to end on a real SQLite ledger: written ``TradeExecuted`` rows are read
  back across strategies and a threshold breach fires; rows outside the
  window do not count.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.tax_guard.window import TAX_GUARD_SOURCE, evaluate_ledger
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.config.models import TaxGuardConfig
from hermes.ports.trade_history import TradeHistoryPort

_NOW = datetime(2026, 11, 30, 12, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"


def _config(*, trade_count_max: int = 500, distinct_assets_max: int = 12) -> TaxGuardConfig:
    return TaxGuardConfig(
        annual_realised_pnl_alert_eur=Decimal("1000"),
        wash_sale_window_days=30,
        max_position_pct_portfolio=Decimal("0.25"),
        recharacterization_window_days=90,
        trade_count_90d_max=trade_count_max,
        distinct_assets_90d_max=distinct_assets_max,
        fee_income_90d_max_eur=Decimal("0"),
    )


def _trade(
    at: datetime, *, base: Currency = Currency.BTC, strategy: str = "swing"
) -> TradeExecuted:
    pair = Pair(base=base, quote=Currency.USDT)
    return TradeExecuted(
        occurred_at=at,
        correlation_id=_CID,
        pair=pair,
        side=TradeSide.BUY,
        qty=Quantity(amount=Decimal("0.01"), asset=base),
        price=Price(amount=Decimal("50000"), pair=pair),
        fee=Money(amount=Decimal("0.10"), currency=Currency.USDT),
        strategy_id=StrategyId(strategy),
    )


class _RecordingHistory:
    """Trade source fake that records the window it was asked for."""

    def __init__(self, trades: tuple[TradeExecuted, ...]) -> None:
        self.trades = trades
        self.asked: list[tuple[datetime, datetime]] = []

    def trades_between(self, start: datetime, end: datetime) -> tuple[TradeExecuted, ...]:
        self.asked.append((start, end))
        return self.trades


def test_reads_exactly_the_rolling_window() -> None:
    """The guard asks for [now - 90 days, now] and nothing wider."""
    history = _RecordingHistory(())
    assert isinstance(history, TradeHistoryPort)
    result = evaluate_ledger(history, _config(), now=_NOW, correlation_id=_CID)
    assert (result, history.asked) == (None, [(_NOW - timedelta(days=90), _NOW)])


def test_breach_from_the_source_fires() -> None:
    """Three trades against a maximum of two produce the guard's anomaly."""
    history = _RecordingHistory(tuple(_trade(_NOW - timedelta(days=d)) for d in (1, 2, 3)))
    result = evaluate_ledger(history, _config(trade_count_max=2), now=_NOW, correlation_id=_CID)
    assert result is not None
    assert result.source == TAX_GUARD_SOURCE


def test_naive_now_is_rejected_before_reading() -> None:
    """A naive clock would shift the window, so nothing is read."""
    history = _RecordingHistory(())
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_ledger(history, _config(), now=datetime(2026, 11, 30), correlation_id=_CID)
    assert history.asked == []


def test_end_to_end_on_a_real_ledger(tmp_path: Path) -> None:
    """Rows written to SQLite are read back across strategies; old rows don't count."""
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    try:
        ledger = SqliteLedger(conn)
        ledger.append_event(_trade(_NOW - timedelta(days=100), base=Currency.SOL))
        ledger.append_event(_trade(_NOW - timedelta(days=10), base=Currency.BTC))
        ledger.append_event(_trade(_NOW - timedelta(days=5), base=Currency.ETH, strategy="other"))

        within = evaluate_ledger(
            ledger, _config(distinct_assets_max=2), now=_NOW, correlation_id=_CID
        )
        breach = evaluate_ledger(
            ledger, _config(distinct_assets_max=1), now=_NOW, correlation_id=_CID
        )
    finally:
        conn.close()

    assert within is None
    assert breach is not None
    assert breach.source == TAX_GUARD_SOURCE
