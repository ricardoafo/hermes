"""Tests for the start-up pricing of pending EUR values in ``hermes serve`` (issue #630).

The FX seam is the scripted ``FakeFx``; the Frankfurter adapter itself is covered
against recorded responses in ``tests/integration/adapters/fx``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from hermes.application.ledger.eur_pricer import EurPricer
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from tests.support.serve_loop import (
    BASE,
    BTC,
    PROJECT_ROOT,
    FakeClock,
    FakeFeed,
    UnavailableBooks,
    build,
    h4_bar,
    history,
    run_once,
)
from tests.unit.application.ledger.fake_fx import FakeFx

_SEED_DAY = date(2024, 1, 1)


def _seed_row(composition: serve_module.Composition) -> tuple[str, str | None, str | None]:
    """Return ``(eur_status, eur_rate, value_eur)`` of the capital seed."""
    row = composition.sqlite_connection.execute(
        "SELECT eur_status, eur_rate, value_eur FROM capital_seeds"
    ).fetchone()
    return (str(row[0]), row[1], row[2])


def test_serve_prices_the_pending_seed_at_boot_when_fx_works(tmp_path: Path) -> None:
    """Invariant (#630): a fresh boot with FX reachable leaves the seed priced, no manual step."""
    fx = FakeFx(rates={_SEED_DAY: (Decimal("1.25"), _SEED_DAY)})
    db = tmp_path / "t.sqlite3"
    composition = build(db, FakeFeed(), BASE, fx=fx)
    assert _seed_row(composition)[0] == "pending"

    with capture_logs() as logs:
        code = run_once(
            composition,
            FakeFeed(),
            until=lambda: any(e["event"] == "serve.eur_priced" for e in logs),
        )

    priced = [entry for entry in logs if entry["event"] == "serve.eur_priced"]
    assert code == 0
    assert [(e["seeds_filled"], e["seeds_pending"]) for e in priced] == [(1, 0)]
    assert len(fx.calls) == 1
    reopened = build(db, FakeFeed(), BASE, fx=fx)
    try:
        assert _seed_row(reopened) == ("ok", "1.25", "4000.000000000000000000000000000000")
    finally:
        reopened.sqlite_connection.close()


def test_serve_keeps_running_and_leaves_the_seed_pending_when_fx_is_down(tmp_path: Path) -> None:
    """Invariant (#630): an FX outage at boot never stops serve and leaves the seed pending."""
    fx = FakeFx(down=True)
    db = tmp_path / "t.sqlite3"
    composition = build(db, FakeFeed(), BASE, fx=fx)

    with capture_logs() as logs:
        code = run_once(
            composition,
            FakeFeed(),
            until=lambda: any(e["event"] == "serve.eur_priced" for e in logs),
        )

    priced = [entry for entry in logs if entry["event"] == "serve.eur_priced"]
    assert code == 0
    assert [(e["seeds_filled"], e["seeds_pending"]) for e in priced] == [(0, 1)]
    reopened = build(db, FakeFeed(), BASE, fx=fx)
    try:
        assert _seed_row(reopened) == ("pending", None, None)
    finally:
        reopened.sqlite_connection.close()


def test_a_ledger_failure_while_pricing_is_logged_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant (#630): any failure of the boot pricing is a warning, never a start-up failure."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise LedgerError(msg="disk full")

    monkeypatch.setattr(serve_module, "backfill_pending_eur", _boom)
    composition = build(tmp_path / "t.sqlite3", FakeFeed(), BASE)

    with capture_logs() as logs:
        serve_module.price_pending_eur_once(composition)

    warnings = [e for e in logs if e["event"] == "serve.eur_pricing_failed"]
    assert [(w["log_level"], w["error"]) for w in warnings] == [("warning", "LedgerError")]
    composition.sqlite_connection.close()


def test_boot_pricing_leaves_an_already_priced_seed_untouched(tmp_path: Path) -> None:
    """Invariant (#630): a second boot never re-prices a seed that already carries its EUR value."""
    first = FakeFx(rates={_SEED_DAY: (Decimal("1.25"), _SEED_DAY)})
    composition = build(tmp_path / "t.sqlite3", FakeFeed(), BASE, fx=first)
    serve_module.price_pending_eur_once(composition)
    priced = _seed_row(composition)
    assert priced[0] == "ok"

    second = FakeFx(rates={_SEED_DAY: (Decimal("2"), _SEED_DAY)})
    again = dataclasses.replace(composition, eur_pricer=EurPricer(second))
    with capture_logs() as logs:
        serve_module.price_pending_eur_once(again)

    assert _seed_row(composition) == priced
    assert second.calls == []
    assert [
        (e["seeds_filled"], e["seeds_pending"]) for e in logs if e["event"] == "serve.eur_priced"
    ] == [(0, 0)]
    composition.sqlite_connection.close()


def _entry_event(pair_quote_ccy: Currency = Currency.USDT, at: datetime = BASE) -> SignalEmitted:
    """Return an entry signal on a pair quoted in ``pair_quote_ccy``."""
    return SignalEmitted(
        occurred_at=at,
        correlation_id="01J9Z8T3M5BQR2FVKN4YK7Q2ZP",
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=Pair(base=Currency.BTC, quote=pair_quote_ccy),
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        atr_context=Decimal("5"),
    )


def test_the_notifier_lookup_reads_the_pricer_cache_and_never_the_provider() -> None:
    """Invariant (668 D-1): the push path answers from memory; cold is None, no FX call."""
    fx = FakeFx(rates={_SEED_DAY: (Decimal("1.25"), _SEED_DAY)})
    pricer = EurPricer(fx)
    lookup = serve_module._eur_rate_lookup(pricer)

    cold = lookup(_entry_event())
    pricer.price(Currency.USDT, _SEED_DAY, today=_SEED_DAY)
    warm = lookup(_entry_event())

    assert (cold, warm, len(fx.calls)) == (None, Decimal("1.25"), 1)


def test_the_notifier_lookup_uses_the_madrid_date_of_the_event() -> None:
    """Invariant (668 D-2): 23:30 UTC on Dec 31 is Jan 1 in Madrid; that day's rate is used."""
    jan1 = date(2024, 1, 1)
    fx = FakeFx(rates={jan1: (Decimal("1.25"), jan1)})
    pricer = EurPricer(fx)
    pricer.price(Currency.USDT, jan1, today=jan1)
    lookup = serve_module._eur_rate_lookup(pricer)

    late_utc = datetime(2023, 12, 31, 23, 30, tzinfo=UTC)
    assert lookup(_entry_event(at=late_utc)) == Decimal("1.25")


def test_the_notifier_lookup_has_no_rate_for_an_event_without_a_pair() -> None:
    """Invariant (668 D-2): events that carry no pair (an anomaly) get no rate and no lookup."""
    fx = FakeFx()
    lookup = serve_module._eur_rate_lookup(EurPricer(fx))
    anomaly = AnomalyDetected(
        occurred_at=BASE,
        correlation_id="01J9Z8T3M5BQR2FVKN4YK7Q2ZP",
        source="swing.risk",
        severity=AnomalySeverity.CRITICAL,
        reason="ledger reconciliation mismatch",
    )
    assert (lookup(anomaly), fx.calls) == (None, [])


def test_serve_gives_both_notifiers_the_pricer_lookup(tmp_path: Path) -> None:
    """Invariant (668 D-2): with credentials set, Telegram and Slack are built with the lookup."""
    env = {
        "HERMES_TELEGRAM__BOT_TOKEN": "token-abc",  # gitleaks:allow
        "HERMES_TELEGRAM__CHAT_ID": "chat-xyz",
        "HERMES_SLACK__BOT_TOKEN": "xoxb-test",  # gitleaks:allow
        "HERMES_SLACK__DEFAULT_CHANNEL": "C0TEST",
    }
    composition = serve_module.build_composition(
        load_config(PROJECT_ROOT / "config", env={}),
        db_path=tmp_path / "t.sqlite3",
        env=env,
        feed=FakeFeed(),
        clock=FakeClock(BASE),
        orderbooks=UnavailableBooks(),
        fx=FakeFx(down=True),
        new_ledger=True,
    )
    try:
        assert composition.telegram is not None
        assert composition.slack is not None
        assert (
            composition.telegram.eur_rate is not None,
            composition.slack.eur_rate is not None,
        ) == (
            True,
            True,
        )
    finally:
        composition.sqlite_connection.close()
        if composition.telegram is not None:
            composition.telegram.close()
        if composition.slack is not None:
            composition.slack.close()


class _ProbeFx(FakeFx):
    """FakeFx that notes how many trades the ledger held at each worker-thread call.

    The fill path prices on the loop thread; only boot pricing runs in a worker.
    """

    def __init__(self, db: Path, *, hang: threading.Event | None = None) -> None:
        """Remember the ledger file to probe, and an optional gate to block calls on."""
        super().__init__(down=True)
        self.db = db
        self.hang = hang
        self.trades_seen: list[int] = []

    def get_for_date(self, from_currency: Currency, to_currency: Currency, on: date) -> FxRate:
        """Record the ledger's trade count, optionally block, then fail like an outage."""
        if threading.current_thread() is not threading.main_thread():
            self.trades_seen.append(_count(self.db, "SELECT COUNT(*) FROM trades"))
            if self.hang is not None:
                self.hang.wait(timeout=30)
        return super().get_for_date(from_currency, to_currency, on)


def _buy(at: datetime) -> Fill:
    """Return a one-BTC BUY fill at ``at``."""
    return Fill(
        order=Order(
            side=OrderSide.BUY,
            pair=BTC,
            qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
            strategy_id=StrategyId("swing-donchian-4h"),
        ),
        executed_qty=Decimal("1"),
        avg_price=Decimal("100"),
        fee=Money(amount=Decimal("0.1"), currency=Currency.USDT),
        timestamp=at,
    )


def _record_pending_buys(composition: serve_module.Composition, times: list[datetime]) -> None:
    """Store one BUY per instant, all left EUR-pending because the pricer is down."""
    recorder = TradeRecorder(composition.ledger, EurPricer(FakeFx(down=True)))
    for index, at in enumerate(times):
        recorder.record(_buy(at), correlation_id=f"pending-{index}")


def _count(db: Path, query: str) -> int:
    """Run a COUNT query on a fresh connection to the ledger file."""
    probe = sqlite3.connect(db)
    try:
        return int(probe.execute(query).fetchone()[0])
    finally:
        probe.close()


def _pending_trades(db: Path) -> int:
    """Count trades still EUR-pending in the ledger file."""
    return _count(db, "SELECT COUNT(*) FROM trades WHERE eur_status = 'pending'")


def _priced(logs: Sequence[Mapping[str, object]]) -> bool:
    """Return whether the boot pricing has logged its outcome."""
    return any(e["event"] == "serve.eur_priced" for e in logs)


def test_boot_pricing_runs_after_catch_up_has_filled(tmp_path: Path) -> None:
    """Invariant (#658): the first FX lookup happens after catch-up recorded its fill."""
    db = tmp_path / "t.sqlite3"
    first = FakeFeed(warm=history(10))
    run_once(build(db, first, h4_bar(10).close_time + timedelta(seconds=1)), first)
    fx = _ProbeFx(db)
    feed = FakeFeed(warm=history(11))
    started = h4_bar(11).close_time + timedelta(seconds=1)
    composition = build(db, feed, started, fx=fx)

    code = run_once(composition, feed, until=lambda: bool(fx.trades_seen))

    assert (code, fx.trades_seen[:1]) == (0, [1])


def test_boot_pricing_stops_at_the_call_cap_and_leaves_trades_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant (#658): with FX down, boot makes at most the capped calls; serve goes on."""
    monkeypatch.setattr(serve_module, "BOOT_EUR_MAX_FX_CALLS", 3)
    fx = FakeFx(down=True)
    db = tmp_path / "t.sqlite3"
    composition = build(db, FakeFeed(), BASE, fx=fx)
    _record_pending_buys(composition, [datetime(2024, 1, 2 + d, 10, tzinfo=UTC) for d in range(8)])

    with capture_logs() as logs:
        code = run_once(composition, FakeFeed(), until=lambda: _priced(logs))

    capped = [e for e in logs if e["event"] == "serve.eur_pricing_capped"]
    assert (code, len(fx.calls), _pending_trades(db)) == (0, 3, 8)
    assert [(e["rates_skipped"], e["rates_total"]) for e in capped] == [(6, 9)]


def test_boot_pricing_stops_at_the_time_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant (#658): once the time budget is spent no further FX call is made."""
    monkeypatch.setattr(serve_module, "BOOT_EUR_BUDGET_SEC", 0.0)
    fx = FakeFx(down=True)
    db = tmp_path / "t.sqlite3"
    composition = build(db, FakeFeed(), BASE, fx=fx)
    _record_pending_buys(composition, [datetime(2024, 1, 2 + d, 10, tzinfo=UTC) for d in range(4)])

    with capture_logs() as logs:
        code = run_once(composition, FakeFeed(), until=lambda: _priced(logs))

    assert (code, fx.calls, _pending_trades(db)) == (0, [], 4)


def test_boot_pricing_tries_a_failing_rate_once_for_many_trades_on_that_date(
    tmp_path: Path,
) -> None:
    """Invariant (#658): ten pending trades on one date cost one FX attempt, plus the seed's."""
    fx = FakeFx(down=True)
    db = tmp_path / "t.sqlite3"
    composition = build(db, FakeFeed(), BASE, fx=fx)
    _record_pending_buys(
        composition, [datetime(2024, 1, 5, 10, minute, tzinfo=UTC) for minute in range(10)]
    )

    with capture_logs() as logs:
        run_once(composition, FakeFeed(), until=lambda: _priced(logs))

    assert (sorted(on for _, _, on in fx.calls), _pending_trades(db)) == (
        [_SEED_DAY, date(2024, 1, 5)],
        10,
    )


def test_cancelling_during_boot_pricing_exits_promptly_and_releases_resources(
    tmp_path: Path,
) -> None:
    """Invariant (#658): a shutdown while FX hangs ends serve in seconds and runs cleanup."""
    gate = threading.Event()
    db = tmp_path / "t.sqlite3"
    fx = _ProbeFx(db, hang=gate)
    feed = FakeFeed()
    composition = build(db, feed, BASE, fx=fx)

    async def scenario() -> tuple[int, float]:
        shutdown = asyncio.Event()
        task = asyncio.create_task(serve_module.run(composition, shutdown))
        while not fx.trades_seen:
            await asyncio.sleep(0.01)
        began = time.monotonic()
        shutdown.set()
        code = await asyncio.wait_for(task, timeout=5)
        elapsed = time.monotonic() - began
        gate.set()
        return code, elapsed

    try:
        code, elapsed = asyncio.run(scenario())
    finally:
        gate.set()

    assert (code, feed.closed, elapsed < 2.0) == (0, True, True)
