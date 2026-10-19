"""Serve records and alerts a recharacterization breach once per daily close (#1181).

ADR-002 Decision 6 promises an engine-level guard over a rolling 90-day
window: trade count, distinct assets and EUR fee income. Serve evaluates it on
the first 1D bar handled for each close time, publishes a breach on its bus
(which records it, #136) and alerts it on every routed channel. Record and
alert only: the pause the ADR also promises is the Code Owner's decision.

Invariants protected here:

- one trade past the trade-count ceiling is one ``CRITICAL`` row and one alert,
  stamped at the daily close, with the counts in the reason;
- a window exactly at the ceiling records and alerts nothing;
- a 4h bar never evaluates the guard;
- the 1D bars of two pairs closing together evaluate it once, on the first of
  them, also across a restart that re-handles the second pair's bar;
- a breach that lasts is recorded once per daily close;
- a guard that cannot read the ledger is logged and skipped; the bar is still
  processed.

Everything from the bar loop to the SQLite ledger file is real; the trades the
window counts are appended to the ledger before the bars arrive. That the
breach changes no decision or fill is proved end to end in
``tests/integration/entrypoints/test_serve_fills.py``.
"""

import asyncio
import dataclasses
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import serve as serve_module
from tests.support.anomaly_rows import recorded_anomalies
from tests.support.serve_loop import (
    BTC,
    ETH,
    FakeFeed,
    RecordingChannel,
    build,
    d1_bar,
    events,
    fresh_strategy,
    h4_bar,
)

pytestmark = pytest.mark.integration

_SOURCE = "tax_guard.recharacterization"
_CEILING = 2
"""Trade-count ceiling of these tests; ADR-002 ships 500, too many rows to seed."""
_NOW = datetime(2024, 3, 1, tzinfo=UTC)
"""The wall clock: later than every bar, so a guard stamped with it would show."""
_IN_WINDOW = datetime(2023, 12, 1, 12, tzinfo=UTC)
"""Inside the 90 days before the first daily close, 2024-01-01T23:59:59.999Z."""


@pytest.fixture(autouse=True)
def _uncached_serve_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give serve a logger ``capture_logs`` can see."""
    monkeypatch.setattr(serve_module, "_log", structlog.get_logger(serve_module.__name__))
    cached = structlog.get_config()["cache_logger_on_first_use"]
    structlog.configure(cache_logger_on_first_use=False)
    yield
    structlog.configure(cache_logger_on_first_use=cached)


def _trade(at: datetime, pair: Pair = BTC) -> TradeExecuted:
    """Return one 0.01-coin BUY at 50000 with a 0.5 USDT fee, stamped ``at``."""
    return TradeExecuted(
        occurred_at=at,
        correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        pair=pair,
        side=TradeSide.BUY,
        qty=Quantity(amount=Decimal("0.01"), asset=pair.base),
        price=Price(amount=Decimal("50000"), pair=pair),
        fee=Money(amount=Decimal("0.5"), currency=Currency.USDT),
        strategy_id=StrategyId("swing-donchian-4h"),
    )


def _loop(
    db: Path, trades: list[TradeExecuted], *, pairs: tuple[Pair, ...] = (BTC,)
) -> tuple[serve_module.Composition, serve_module._BarLoop, RecordingChannel]:
    """Return a loop over ``db`` with ``trades`` on record and a ceiling of two trades."""
    telegram = RecordingChannel()
    base = build(db, FakeFeed(), _NOW, telegram=telegram)
    guard = base.config.tax_guard.model_copy(update={"trade_count_90d_max": _CEILING})
    composition = dataclasses.replace(
        base,
        config=base.config.model_copy(update={"tax_guard": guard}),
        strategies={pair: fresh_strategy() for pair in pairs},
        pairs=pairs,
    )
    for trade in trades:
        composition.ledger.append_event(trade)
    return composition, serve_module._BarLoop(composition), telegram


def _recorded(db: Path) -> list[tuple[str, str, str, str]]:
    """Return ``(occurred_at, source, severity, reason)`` of every tax-guard row, in order."""
    return [
        (str(p["occurred_at"]), str(p["source"]), str(p["severity"]), str(p["reason"]))
        for p in recorded_anomalies(db)
        if p["source"] == _SOURCE
    ]


def _alerted(channel: RecordingChannel) -> list[tuple[datetime, str]]:
    """Return ``(occurred_at, reason)`` of every tax-guard alert the channel received."""
    return [
        (e.occurred_at, e.reason)
        for e in channel.seen
        if isinstance(e, AnomalyDetected) and e.source == _SOURCE
    ]


_THREE_PAST_TWO = "recharacterization threshold breached: trade_count=3 > 2"


# SEV-1: S12 an alert (the ADR-002 Decision 6 tax guard) that never fires is bypassed silently
def test_one_trade_past_the_ceiling_records_one_critical_row_at_the_daily_close(
    tmp_path: Path,
) -> None:
    """Three trades in the window against a ceiling of two: one row, stamped at the close.

    The fourth trade is after the close, so a window ending at the wall clock
    instead of the bar's close would count four.
    """
    db = tmp_path / "ledger.sqlite3"
    trades = [_trade(_IN_WINDOW + timedelta(hours=h)) for h in range(3)]
    trades.append(_trade(datetime(2024, 1, 5, tzinfo=UTC)))
    composition, loop, _ = _loop(db, trades)

    asyncio.run(loop.handle(d1_bar(0)))
    composition.sqlite_connection.close()

    assert _recorded(db) == [
        ("2024-01-01T23:59:59.999000+00:00", _SOURCE, "CRITICAL", _THREE_PAST_TWO)
    ]


# SEV-1: S12 an alert (the ADR-002 Decision 6 tax guard) that never fires is bypassed silently
def test_one_trade_past_the_ceiling_is_alerted_once_on_the_routed_channel(
    tmp_path: Path,
) -> None:
    """The breach reaches Telegram once, with the same close time and reason as its row."""
    db = tmp_path / "ledger.sqlite3"
    composition, loop, telegram = _loop(
        db, [_trade(_IN_WINDOW + timedelta(hours=h)) for h in range(3)]
    )

    asyncio.run(loop.handle(d1_bar(0)))
    composition.sqlite_connection.close()

    assert _alerted(telegram) == [(d1_bar(0).close_time, _THREE_PAST_TWO)]


def test_a_window_exactly_at_the_ceiling_records_and_alerts_nothing(tmp_path: Path) -> None:
    """Two trades against a ceiling of two hold: ADR-002 fires on more than the ceiling."""
    db = tmp_path / "ledger.sqlite3"
    composition, loop, telegram = _loop(
        db, [_trade(_IN_WINDOW + timedelta(hours=h)) for h in range(2)]
    )

    asyncio.run(loop.handle(d1_bar(0)))
    composition.sqlite_connection.close()

    assert (_recorded(db), _alerted(telegram), len(events(db, "BarProcessed"))) == ([], [], 1)


def test_a_4h_bar_never_evaluates_the_guard(tmp_path: Path) -> None:
    """A breached window is not recorded on a 4h bar: the guard runs on daily closes only."""
    db = tmp_path / "ledger.sqlite3"
    trades = [_trade(_IN_WINDOW + timedelta(days=40, hours=h)) for h in range(3)]
    composition, loop, telegram = _loop(db, trades)

    asyncio.run(loop.handle(h4_bar(0)))
    composition.sqlite_connection.close()

    assert (_recorded(db), _alerted(telegram), len(events(db, "BarProcessed"))) == ([], [], 1)


def test_two_pairs_closing_the_same_day_record_the_breach_once(tmp_path: Path) -> None:
    """BTC's and ETH's 1D bars share a close time; only the first one evaluates the window."""
    db = tmp_path / "ledger.sqlite3"
    trades = [_trade(_IN_WINDOW + timedelta(hours=h)) for h in range(3)]
    composition, loop, _ = _loop(db, trades, pairs=(BTC, ETH))

    asyncio.run(loop.handle(d1_bar(0, BTC)))
    asyncio.run(loop.handle(d1_bar(0, ETH)))
    composition.sqlite_connection.close()

    assert [row[0] for row in _recorded(db)] == ["2024-01-01T23:59:59.999000+00:00"]


def test_the_first_daily_bar_of_a_close_decides_the_day_for_every_pair(tmp_path: Path) -> None:
    """A trade recorded between BTC's and ETH's bars of one close is not evaluated by ETH's.

    BTC's bar sees two trades, at the ceiling; ETH's bar would see three. The
    day is evaluated once, so the breach waits for the next daily close.
    """
    db = tmp_path / "ledger.sqlite3"
    trades = [_trade(_IN_WINDOW + timedelta(hours=h)) for h in range(2)]
    composition, loop, _ = _loop(db, trades, pairs=(BTC, ETH))

    asyncio.run(loop.handle(d1_bar(0, BTC)))
    composition.ledger.append_event(_trade(_IN_WINDOW + timedelta(hours=2), ETH))
    asyncio.run(loop.handle(d1_bar(0, ETH)))
    composition.sqlite_connection.close()

    assert _recorded(db) == []


def test_a_restart_that_re_handles_the_second_pairs_bar_records_nothing_new(
    tmp_path: Path,
) -> None:
    """A fresh loop handling ETH's bar of a recorded day neither records nor alerts it again."""
    db = tmp_path / "ledger.sqlite3"
    trades = [_trade(_IN_WINDOW + timedelta(hours=h)) for h in range(3)]
    composition, first, _ = _loop(db, trades, pairs=(BTC, ETH))
    asyncio.run(first.handle(d1_bar(0, BTC)))
    telegram = RecordingChannel()
    restarted = dataclasses.replace(
        composition,
        alert_channels=dataclasses.replace(composition.alert_channels, telegram=telegram),
    )

    asyncio.run(serve_module._BarLoop(restarted).handle(d1_bar(0, ETH)))
    composition.sqlite_connection.close()

    assert (len(_recorded(db)), _alerted(telegram)) == (1, [])


def test_a_breach_that_lasts_is_recorded_once_per_daily_close(tmp_path: Path) -> None:
    """Days 0 and 1 both see the three trades: one row each, at each day's close."""
    db = tmp_path / "ledger.sqlite3"
    composition, loop, _ = _loop(db, [_trade(_IN_WINDOW + timedelta(hours=h)) for h in range(3)])

    asyncio.run(loop.handle(d1_bar(0)))
    asyncio.run(loop.handle(d1_bar(1)))
    composition.sqlite_connection.close()

    assert [row[0] for row in _recorded(db)] == [
        "2024-01-01T23:59:59.999000+00:00",
        "2024-01-02T23:59:59.999000+00:00",
    ]


def _unreadable_trade_row(db: Path) -> None:
    """Append a trade row of an unknown schema version, which the trade reader refuses."""
    connection = sqlite3.connect(db)
    try:
        with connection:
            connection.execute(
                "INSERT INTO events (event_type, event_schema_version, correlation_id, "
                "occurred_at, payload_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "TradeExecuted",
                    99,
                    "01JABCDEFGHJKMNPQRSTVWXYZ0",
                    _IN_WINDOW.isoformat(),
                    "{}",
                    _IN_WINDOW.isoformat(),
                ),
            )
    finally:
        connection.close()


def test_a_guard_that_cannot_read_the_ledger_is_logged_and_the_bar_still_processed(
    tmp_path: Path,
) -> None:
    """The failure is one warning naming the error, as for the correlations; serve goes on."""
    db = tmp_path / "ledger.sqlite3"
    composition, loop, _ = _loop(db, [])
    _unreadable_trade_row(db)

    with capture_logs() as logs:
        asyncio.run(loop.handle(d1_bar(0)))
    composition.sqlite_connection.close()

    failed = [
        (line["log_level"], line["close_time"], line["error"])
        for line in logs
        if line["event"] == "serve.tax_guard.failed"
    ]
    assert (failed, len(events(db, "BarProcessed"))) == (
        [("warning", "2024-01-01T23:59:59.999000+00:00", "LedgerError")],
        1,
    )
