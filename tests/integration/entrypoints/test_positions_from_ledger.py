"""From a recorded fill to ``hermes positions`` output (#546, slice 1).

The fill goes through the real recording path: :class:`TradeRecorder` prices it
in EUR (scripted ECB rate) and writes the trade, its lot and its event; the
portfolio snapshot moves through ``after_fill`` and ``after_bar`` and is stored
as marks; the entry signal is appended as the serve loop does. The CLI then
reads that ledger file read-only.

Reference: openspec/changes/ledger-read-model/change.md (A-5.2).
"""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import cli as cli_module
from hermes.entrypoints.console import DEMO_ENV_VAR
from tests.unit.application.ledger.fake_fx import FakeFx

pytestmark = pytest.mark.integration

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_SWING = StrategyId("swing-donchian-4h")
_T0 = datetime(2026, 10, 1, 8, tzinfo=UTC)
_H4 = timedelta(hours=4)
_ONE_MS = timedelta(milliseconds=1)


def _bar(open_time: datetime, close: str) -> Bar:
    price = Decimal(close)
    return Bar(
        pair=_BTC,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
        close_time=open_time + _H4 - _ONE_MS,
    )


def _record_session(path: Path, *, fx: FakeFx) -> None:
    conn = sqlite3.connect(path)
    ledger = SqliteLedger(conn)
    entry_bar = _bar(_T0 - _H4, "50000")
    ledger.append_event(
        SignalEmitted(
            occurred_at=entry_bar.close_time,
            correlation_id="sig-1",
            strategy_id=_SWING,
            pair=_BTC,
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=Decimal(50000),
            stop_price=Decimal(48000),
            atr_context=Decimal(1000),
        )
    )
    fill = Fill(
        order=Order(
            side=OrderSide.BUY,
            pair=_BTC,
            qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
            strategy_id=_SWING,
        ),
        executed_qty=Decimal("0.5"),
        avg_price=Decimal(50000),
        fee=Money(amount=Decimal(25), currency=Currency.USDT),
        timestamp=_T0 + timedelta(seconds=5),
    )
    TradeRecorder(ledger, EurPricer(fx)).record(fill, correlation_id="fill-1")
    snapshot = (
        PortfolioSnapshot.seed(_SWING, Money(amount=Decimal(100000), currency=Currency.USDT))
        .after_fill(fill)
        .unwrap()
    )
    for i, close in enumerate(("53000", "52000")):
        bar = _bar(_T0 + _H4 * i, close)
        snapshot = snapshot.after_bar(bar)
        ledger.record_portfolio_mark(
            PortfolioMark(
                snapshot=snapshot, pair=_BTC, interval=bar.interval, bar_close_time=bar.close_time
            )
        )
    conn.close()


def _positions(args: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    monkeypatch.delenv(DEMO_ENV_VAR, raising=False)
    monkeypatch.setenv("COLUMNS", "120")
    stream = io.StringIO()
    return cli_module.main(["positions", *args], stream=stream), stream.getvalue()


def test_recorded_fill_shows_in_hermes_positions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The priced fill appears with its EUR entry, the latest price and the live net."""
    path = tmp_path / "hermes.sqlite3"
    _record_session(
        path, fx=FakeFx(rates={date(2026, 10, 1): (Decimal("1.25"), date(2026, 10, 1))})
    )
    rc, out = _positions(["--db", str(path)], monkeypatch)
    assert (rc, "BTC" in out, "€40,000 → €41,600" in out, "+€759.20 net" in out) == (
        0,
        True,
        True,
        True,
    )


def test_recorded_fill_detail_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``positions BTC --json`` carries the ledger's cost figures and no demo tag."""
    path = tmp_path / "hermes.sqlite3"
    _record_session(
        path, fx=FakeFx(rates={date(2026, 10, 1): (Decimal("1.25"), date(2026, 10, 1))})
    )
    rc, out = _positions(["BTC", "--json", f"--db={path}"], monkeypatch)
    data = json.loads(out)
    pnl = data["pnl"]
    assert (
        rc,
        data["demo_scenario"],
        Decimal(pnl["fees_paid_eur"]),
        Decimal(pnl["net_eur"]),
    ) == (0, None, Decimal(20), Decimal("759.2"))


def test_fill_without_ecb_rate_shows_eur_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the FX provider down the trade is stored pending and the row says so."""
    path = tmp_path / "hermes.sqlite3"
    _record_session(path, fx=FakeFx(down=True))
    rc, out = _positions(["--db", str(path)], monkeypatch)
    assert (rc, "EUR pending" in out, "€" in out) == (0, True, False)
