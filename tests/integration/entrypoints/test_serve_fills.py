"""Integration tests: a closed bar becomes a simulated fill in the ledger (#353).

Everything from the bar feed to the SQLite ledger is real (strategy, fill
kernel, router, walk fill model, trade recorder, ledger) except three seams:
the scripted feed, a clock that steps to a few seconds after each live bar's
close (as the live poller delivers), and a depth source that answers a book
around the last close. The depth seam is covered against a recorded Binance
response in ``tests/unit/adapters/market_data/test_binance_depth_snapshot.py``.

Invariants (change 353-fill-kernel-in-serve R-1, R-4, R-5):

- A live entry signal produces one ``TradeExecuted`` v2 trade with the
  strategy id, a ``trades`` row and one live ``FillQualityRecorded`` row,
  joined by one correlation id; the exit sells exactly what was bought.
- After a restart the portfolio is rebuilt from the recorded trades, holds
  the same position and the exit closes it.
- A bar re-evaluated after a crash before its cursor row fills nothing twice.
- The portfolio mark stored for a bar (#511) is taken after that bar's fill.
- On a first boot, a signal of a bar that closed just before the start fills
  at the capital seed's time, so the seed lot pays for it (405-usdt-fifo D-1).
- Pushed notifications carry signals only, and fill log lines carry no
  equity, P&L, drawdown or drift.
- An entry refused on a stale book leaves the strategy flat (#707): no later
  exit is signalled, declined ``no-position`` or notified, and the pair
  enters again on the next breakout.
- An exit declined ``no-book`` (a zero-volume bar while the depth endpoint is
  down) leaves the strategy in the position (#706): the next crash bar, with
  the book back, exits it again with the original entry price and sells the
  coin once, on the same boot or after a restart.
- An ``AnomalyDetected`` published on serve's bus is recorded in the ledger
  and changes no signal, fill, position or notification (#136, record-only).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from hermes.adapters.clock import SimulatedClock
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import NetworkError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.event_bus import EventBus
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import serve as serve_module
from tests.support.serve_loop import (
    BTC,
    FakeFeed,
    RecordingChannel,
    build,
    events,
    h4_bar,
    history,
    live,
    make_h4_bar,
    run_once,
    signal_kinds,
)

pytestmark = pytest.mark.integration

_DELIVERY_DELAY = timedelta(seconds=6)


@pytest.fixture(autouse=True)
def _uncached_serve_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give serve a logger ``capture_logs`` can see.

    A test that configured structlog with ``cache_logger_on_first_use`` earlier
    in the session pins serve's module logger to that processor chain, and
    ``capture_logs`` then records nothing.
    """
    monkeypatch.setattr(serve_module, "_log", structlog.get_logger(serve_module.__name__))
    cached = structlog.get_config()["cache_logger_on_first_use"]
    structlog.configure(cache_logger_on_first_use=False)
    yield
    structlog.configure(cache_logger_on_first_use=cached)


_STRATEGY = "swing-donchian-4h"


class _LiveBooks:
    """Depth source answering a deep book one unit around the last close."""

    def __init__(self, clock: SimulatedClock) -> None:
        """Start with no price; the feed sets it as bars arrive."""
        self.clock = clock
        self.mid = Decimal("0")

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return a two-level book around ``mid`` stamped now."""
        return Orderbook(
            pair=pair,
            bids=(
                OrderbookLevel(price=self.mid - 1, quantity=Decimal("10")),
                OrderbookLevel(price=self.mid - 2, quantity=Decimal("100")),
            ),
            asks=(
                OrderbookLevel(price=self.mid + 1, quantity=Decimal("10")),
                OrderbookLevel(price=self.mid + 2, quantity=Decimal("100")),
            ),
            snapshot_time=self.clock.now(),
        )

    async def aclose(self) -> None:
        """Nothing to release."""


class _SteppingFeed(FakeFeed):
    """Scripted feed that delivers each live bar a few seconds after its close."""

    def __init__(
        self,
        clock: SimulatedClock,
        books: _LiveBooks,
        warm: Sequence[Bar] = (),
        live_bars: Sequence[Bar] = (),
    ) -> None:
        """Script the feed and remember the clock and book to move."""
        super().__init__(warm=warm, live_bars=live_bars)
        self.clock = clock
        self.books = books

    async def stream_closed_bars(
        self,
        pairs: Sequence[Pair],
        intervals: Sequence[KlineInterval],
        *,
        resume_after: Mapping[tuple[Pair, KlineInterval], datetime] | None = None,
    ) -> AsyncIterator[Bar]:
        """Step the clock and the book to each bar, then yield it."""
        for bar in self.live_bars:
            self.clock.set(bar.close_time + _DELIVERY_DELAY)
            self.books.mid = bar.close
            yield bar
        self.drained.set()
        await asyncio.Event().wait()


def _boot(
    db: Path,
    *,
    warm_upto: int,
    live_from: int,
    live_to: int,
    telegram: RecordingChannel | None = None,
    feed_class: type[_SteppingFeed] = _SteppingFeed,
) -> tuple[serve_module.Composition, _SteppingFeed]:
    """Build one boot of the loop over ``db`` with live books."""
    clock = SimulatedClock(start=h4_bar(warm_upto).close_time + _DELIVERY_DELAY)
    books = _LiveBooks(clock)
    books.mid = h4_bar(warm_upto).close
    feed = feed_class(clock, books, warm=history(warm_upto), live_bars=live(live_from, live_to))
    composition = build(db, feed, clock.now(), telegram=telegram, books=books, clock=clock)
    return composition, feed


def _payloads(db: Path, event_type: str) -> list[dict[str, object]]:
    """Return the decoded payloads of one event type, oldest first."""
    return [json.loads(row["payload_json"]) for row in events(db, event_type)]


def _trade_rows(db: Path) -> list[tuple[str, str]]:
    """Return ``(side, correlation_id)`` of every trades row."""
    connection = sqlite3.connect(db)
    try:
        return connection.execute("SELECT side, correlation_id FROM trades ORDER BY id").fetchall()
    finally:
        connection.close()


# SEV-1: real-money guard (AGENTS.md prohibition 7) — a live signal must end as a
# simulated ledger fill under its strategy, the only effect serve may have.
def test_live_entry_becomes_one_strategy_trade_with_a_live_quality_row(tmp_path: Path) -> None:
    """Bar 11's entry is one BUY trade, one trades row and one live quality row, one id."""
    db = tmp_path / "ledger.sqlite3"
    composition, feed = _boot(db, warm_upto=10, live_from=11, live_to=12)

    assert run_once(composition, feed) == 0

    (trade,) = _payloads(db, "TradeExecuted")
    (quality,) = _payloads(db, "FillQualityRecorded")
    assert (
        trade["side"],
        trade["strategy_id"],
        _trade_rows(db),
        quality["book_source"],
        quality["correlation_id"],
    ) == (
        "BUY",
        {"value": _STRATEGY},
        [("BUY", trade["correlation_id"])],
        "live",
        trade["correlation_id"],
    )


def test_first_boot_fill_of_a_bar_closed_before_the_start_is_paid_by_the_seed(
    tmp_path: Path,
) -> None:
    """Bar 11 closed a second before the first boot: its BUY is stamped at the seed time.

    Stamped at the bar close it would precede the seed, and the USDT FIFO
    replay would find no capital and stop the loop.
    """
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(10), live_bars=live(11, 11))
    started = h4_bar(11).close_time + timedelta(seconds=1)

    code = run_once(build(db, feed, started), feed)

    connection = sqlite3.connect(db)
    try:
        seed = SqliteLedger(connection).capital_seed(StrategyId(_STRATEGY))
    finally:
        connection.close()
    (trade,) = events(db, "TradeExecuted")
    assert (code, seed is not None and seed.seeded_at, trade["occurred_at"]) == (
        0,
        started,
        started.isoformat(),
    )


def test_the_entry_bars_portfolio_mark_holds_the_bought_position(tmp_path: Path) -> None:
    """The mark of bar 11 is written after its fill, so it already holds the BUY."""
    db = tmp_path / "ledger.sqlite3"
    composition, feed = _boot(db, warm_upto=10, live_from=11, live_to=11)

    run_once(composition, feed)

    (trade,) = _payloads(db, "TradeExecuted")
    bar = h4_bar(11)
    connection = sqlite3.connect(db)
    try:
        marks = SqliteLedger(connection).portfolio_marks(
            StrategyId(_STRATEGY), bar.close_time, bar.close_time + timedelta(milliseconds=1)
        )
    finally:
        connection.close()
    held = [
        str(m.snapshot.positions[BTC].quantity)
        for m in marks
        if m.interval is KlineInterval.H4 and BTC in m.snapshot.positions
    ]
    assert held == [trade["qty"]["amount"]]  # type: ignore[index]


def test_live_entry_fills_at_the_ask_of_the_live_book(tmp_path: Path) -> None:
    """The book around close 2000 fills the BUY at its 2001 ask, not at the §6.2 price."""
    db = tmp_path / "ledger.sqlite3"
    composition, feed = _boot(db, warm_upto=10, live_from=11, live_to=12)

    run_once(composition, feed)

    (trade,) = _payloads(db, "TradeExecuted")
    assert Decimal(trade["price"]["amount"]) == Decimal("2001")  # type: ignore[index]


def test_live_exit_sells_exactly_what_the_entry_bought(tmp_path: Path) -> None:
    """Through bar 14 the crash exit sells the bought quantity and the pair is flat."""
    db = tmp_path / "ledger.sqlite3"
    composition, feed = _boot(db, warm_upto=10, live_from=11, live_to=14)

    run_once(composition, feed)

    buy, sell = _payloads(db, "TradeExecuted")
    assert ((buy["side"], sell["side"]), sell["qty"], composition.tracker.snapshot.positions) == (
        ("BUY", "SELL"),
        buy["qty"],
        {},
    )


def test_restart_rebuilds_the_position_from_recorded_trades(tmp_path: Path) -> None:
    """Boot 2 starts holding exactly the quantity boot 1 bought."""
    db = tmp_path / "ledger.sqlite3"
    first, first_feed = _boot(db, warm_upto=10, live_from=11, live_to=12)
    run_once(first, first_feed)
    (buy,) = _payloads(db, "TradeExecuted")

    second, _ = _boot(db, warm_upto=12, live_from=13, live_to=14)
    held = second.tracker.snapshot.positions[BTC].quantity
    second.sqlite_connection.close()

    assert str(held) == buy["qty"]["amount"]  # type: ignore[index]


def test_exit_after_a_restart_closes_the_rebuilt_position(tmp_path: Path) -> None:
    """The exit on boot 2 sells the position boot 1 opened, once."""
    db = tmp_path / "ledger.sqlite3"
    first, first_feed = _boot(db, warm_upto=10, live_from=11, live_to=12)
    run_once(first, first_feed)
    second, second_feed = _boot(db, warm_upto=12, live_from=13, live_to=14)

    run_once(second, second_feed)

    sides = [p["side"] for p in _payloads(db, "TradeExecuted")]
    assert (sides, second.tracker.snapshot.positions) == (["BUY", "SELL"], {})


def test_restart_restores_the_mark_to_market_peak_equity(tmp_path: Path) -> None:
    """Invariant (#828, integrity rules 4.1): a restart keeps the peak a bar mark set.

    Bar 12 marks the position at 2400 (the high), bar 13 falls back to 2200.
    A fills-only rebuild would restart from the seed peak of 5000, so the
    RO-08 drawdown would read low; the restart must hold the peak boot 1 had.
    """
    db = tmp_path / "ledger.sqlite3"
    first, first_feed = _boot(db, warm_upto=10, live_from=11, live_to=11)
    first_feed.live_bars = [
        h4_bar(11),
        make_h4_bar(12, "2400"),
        make_h4_bar(13, "2200"),
    ]
    run_once(first, first_feed)
    live_peak = first.tracker.snapshot.peak_equity
    connection = sqlite3.connect(db)
    try:
        (marked_high,) = connection.execute(
            "SELECT MAX(peak_equity) FROM portfolio_marks"
        ).fetchone()
    finally:
        connection.close()

    second, _ = _boot(db, warm_upto=13, live_from=14, live_to=14)
    state = second.tracker.state()
    second.sqlite_connection.close()

    assert live_peak > Decimal("5000")
    assert (state.peak_value, Decimal(str(marked_high))) == (live_peak, live_peak)


def test_bar_re_evaluated_after_a_crash_fills_nothing_twice(tmp_path: Path) -> None:
    """With the cursor rows of bars 11-12 lost, boot 2 re-evaluates bar 11 and adds no trade."""
    db = tmp_path / "ledger.sqlite3"
    first, first_feed = _boot(db, warm_upto=10, live_from=11, live_to=12)
    run_once(first, first_feed)
    connection = sqlite3.connect(db)
    connection.execute(
        "DELETE FROM events WHERE event_type = 'BarProcessed' "
        "AND payload_json LIKE '%\"4h\"%' AND occurred_at >= ?",
        (h4_bar(11).close_time.isoformat(),),
    )
    connection.commit()
    connection.close()
    second, second_feed = _boot(db, warm_upto=12, live_from=13, live_to=12)

    with capture_logs() as logs:
        run_once(second, second_feed)

    assert (
        len(_trade_rows(db)),
        [e["event"] for e in logs if str(e["event"]).startswith("serve.fill.")],
    ) == (1, ["serve.fill.already_filled"])


def test_pushed_notifications_carry_signals_only(tmp_path: Path) -> None:
    """A run with two fills pushes the two signals and nothing about the fills."""
    db = tmp_path / "ledger.sqlite3"
    telegram = RecordingChannel()
    composition, feed = _boot(db, warm_upto=10, live_from=11, live_to=14, telegram=telegram)

    run_once(composition, feed)

    assert [type(e) for e in telegram.seen] == [SignalEmitted, SignalEmitted]


def test_fill_log_lines_carry_no_pnl_equity_drawdown_or_drift(tmp_path: Path) -> None:
    """The journal is read by AI sessions, so fill lines stay blind (ruling 1, rule 4)."""
    db = tmp_path / "ledger.sqlite3"
    composition, feed = _boot(db, warm_upto=10, live_from=11, live_to=14)

    with capture_logs() as logs:
        run_once(composition, feed)

    fill_lines = [e for e in logs if str(e["event"]).startswith("serve.fill.")]
    blind = {"pnl", "equity", "drawdown", "drift", "drift_bps", "realized_pnl", "unrealized_pnl"}
    assert (len(fill_lines), [sorted(set(e) & blind) for e in fill_lines]) == (2, [[], []])


class _StaleFirstBooks(_LiveBooks):
    """Live books whose first snapshot is ten seconds old, so RO-04 refuses that entry."""

    def __init__(self, clock: SimulatedClock) -> None:
        """Start with the stale snapshot still to serve."""
        super().__init__(clock)
        self.served = 0

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return the live book, aged past the two-second limit on the first call."""
        book = await super().snapshot(pair)
        self.served += 1
        if self.served == 1:
            return dataclasses.replace(
                book, snapshot_time=book.snapshot_time - timedelta(seconds=10)
            )
        return book


def _refused_entry_run(
    db: Path, after: Sequence[Bar]
) -> tuple[RecordingChannel, list[dict[str, object]]]:
    """Run bar 11 (entry refused on a stale book) then ``after``; return telegram and logs."""
    clock = SimulatedClock(start=h4_bar(10).close_time + _DELIVERY_DELAY)
    books = _StaleFirstBooks(clock)
    books.mid = h4_bar(10).close
    feed = _SteppingFeed(clock, books, warm=history(10), live_bars=[h4_bar(11), *after])
    telegram = RecordingChannel()
    composition = build(db, feed, clock.now(), telegram=telegram, books=books, clock=clock)
    with capture_logs() as logs:
        run_once(composition, feed)
    return telegram, logs


def _declined_rules(logs: list[dict[str, object]]) -> list[tuple[object, object]]:
    """Return ``(kind, rule)`` of every ``serve.fill.declined`` line (RO-04 logs ``HALT``)."""
    return [(e["kind"], e["rule"]) for e in logs if e["event"] == "serve.fill.declined"]


def test_refused_entry_raises_no_later_exit_or_notification(tmp_path: Path) -> None:
    """A stale-book entry is the only decline: the crash bar signals and notifies no exit."""
    db = tmp_path / "ledger.sqlite3"

    telegram, logs = _refused_entry_run(db, live(12, 14))

    kinds = [p["kind"] for p in _payloads(db, "SignalEmitted")]
    assert (kinds, signal_kinds(telegram), _trade_rows(db)) == (["entry"], ["entry"], [])
    assert _declined_rules(logs) == [("entry", "HALT")]


def test_pair_enters_again_after_a_refused_entry(tmp_path: Path) -> None:
    """After the refused entry, the next breakout is a new entry that fills one BUY."""
    db = tmp_path / "ledger.sqlite3"
    breakout = dataclasses.replace(
        h4_bar(12),
        open=Decimal("3000"),
        high=Decimal("3002"),
        low=Decimal("2998"),
        close=Decimal("3000"),
    )

    telegram, logs = _refused_entry_run(db, [breakout])

    signals = [(p["kind"], p["entry_price"]) for p in _payloads(db, "SignalEmitted")]
    sides = [p["side"] for p in _payloads(db, "TradeExecuted")]
    assert (signals, signal_kinds(telegram), sides) == (
        [("entry", "2000"), ("entry", "3000")],
        ["entry", "entry"],
        ["BUY"],
    )
    assert _declined_rules(logs) == [("entry", "HALT")]


class _DownOnSecondCallBooks(_LiveBooks):
    """Live books whose second snapshot fails, like a depth endpoint outage."""

    def __init__(self, clock: SimulatedClock) -> None:
        """Start with no snapshot served."""
        super().__init__(clock)
        self.calls = 0

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Fail on the second call; answer the live book otherwise."""
        self.calls += 1
        if self.calls == 2:
            raise NetworkError(f"no depth for {pair} in tests")
        return await super().snapshot(pair)


def _declined_exit_boot(
    db: Path, live_bars: Sequence[Bar], *, warm_upto: int = 10
) -> tuple[serve_module.Composition, _SteppingFeed]:
    """Build a boot whose second depth read fails, delivering ``live_bars``."""
    clock = SimulatedClock(start=h4_bar(warm_upto).close_time + _DELIVERY_DELAY)
    books = _DownOnSecondCallBooks(clock)
    books.mid = h4_bar(warm_upto).close
    feed = _SteppingFeed(clock, books, warm=history(warm_upto), live_bars=live_bars)
    return build(db, feed, clock.now(), books=books, clock=clock), feed


def _zero_volume_crash() -> Bar:
    """Bar 14, the crash, with no volume: with the depth down it has no book."""
    return dataclasses.replace(h4_bar(14), volume=Decimal("0"))


def test_exit_declined_for_no_book_is_retried_on_the_next_bar(tmp_path: Path) -> None:
    """Bar 14's exit is declined no-book; bar 15 exits again and sells the coin, once (#706)."""
    db = tmp_path / "ledger.sqlite3"
    composition, feed = _declined_exit_boot(
        db, [*live(11, 13), _zero_volume_crash(), h4_bar(15, crash=True)]
    )

    with capture_logs() as logs:
        run_once(composition, feed)

    signals = [(p["kind"], p["entry_price"]) for p in _payloads(db, "SignalEmitted")]
    sides = [p["side"] for p in _payloads(db, "TradeExecuted")]
    assert (signals, sides, composition.tracker.snapshot.positions) == (
        [("entry", "2000"), ("exit", "2000"), ("exit", "2000")],
        ["BUY", "SELL"],
        {},
    )
    assert _declined_rules(logs) == [("exit", "no-book")]


def test_exit_declined_before_a_restart_is_retried_after_it(tmp_path: Path) -> None:
    """Boot 2 restores the entry past the declined exit, so bar 15 sells the coin (#706)."""
    db = tmp_path / "ledger.sqlite3"
    first, first_feed = _declined_exit_boot(db, [*live(11, 13), _zero_volume_crash()])
    run_once(first, first_feed)
    second, second_feed = _boot(db, warm_upto=14, live_from=15, live_to=15)
    second_feed.live_bars = [h4_bar(15, crash=True)]

    run_once(second, second_feed)

    kinds = [p["kind"] for p in _payloads(db, "SignalEmitted")]
    sides = [p["side"] for p in _payloads(db, "TradeExecuted")]
    assert (kinds, sides, second.tracker.snapshot.positions) == (
        ["entry", "exit", "exit"],
        ["BUY", "SELL"],
        {},
    )


class _AnomalousFeed(_SteppingFeed):
    """Stepping feed that publishes one CRITICAL anomaly on serve's bus before each live bar."""

    bus: EventBus | None = None

    async def stream_closed_bars(
        self,
        pairs: Sequence[Pair],
        intervals: Sequence[KlineInterval],
        *,
        resume_after: Mapping[tuple[Pair, KlineInterval], datetime] | None = None,
    ) -> AsyncIterator[Bar]:
        """Publish the anomaly, then yield the bar the stepping feed delivers."""
        async for bar in super().stream_closed_bars(pairs, intervals, resume_after=resume_after):
            assert self.bus is not None
            self.bus.publish(
                AnomalyDetected(
                    occurred_at=self.clock.now(),
                    correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
                    source="test.injected",
                    severity=AnomalySeverity.CRITICAL,
                    reason=f"injected before {bar.close_time.isoformat()}",
                )
            )
            yield bar


_RUN_SPECIFIC = frozenset({"correlation_id", "payload_sha256"})


def _decisions(db: Path) -> dict[str, list[dict[str, object]]]:
    """Return the signal, trade and fill-quality payloads without their per-run ids."""
    return {
        event_type: [
            {k: v for k, v in payload.items() if k not in _RUN_SPECIFIC}
            for payload in _payloads(db, event_type)
        ]
        for event_type in ("SignalEmitted", "TradeExecuted", "FillQualityRecorded")
    }


# SEV-1: real-money guard (AGENTS.md prohibition 7) — an anomaly is recorded only;
# it must never change what serve decides or fills (binding rule 2, issue #136).
def test_an_anomaly_on_the_bus_is_recorded_and_changes_no_decision_or_fill(
    tmp_path: Path,
) -> None:
    """Entry and exit run with a CRITICAL anomaly before every bar: same fills, rows recorded."""
    plain_db = tmp_path / "plain.sqlite3"
    plain_channel = RecordingChannel()
    plain, plain_feed = _boot(
        plain_db, warm_upto=10, live_from=11, live_to=14, telegram=plain_channel
    )
    assert run_once(plain, plain_feed) == 0

    noisy_db = tmp_path / "noisy.sqlite3"
    noisy_channel = RecordingChannel()
    noisy, noisy_feed = _boot(
        noisy_db,
        warm_upto=10,
        live_from=11,
        live_to=14,
        telegram=noisy_channel,
        feed_class=_AnomalousFeed,
    )
    assert isinstance(noisy_feed, _AnomalousFeed)
    noisy_feed.bus = noisy.event_bus
    assert run_once(noisy, noisy_feed) == 0

    recorded = _payloads(noisy_db, "AnomalyDetected")
    assert [(r["source"], r["severity"]) for r in recorded] == [("test.injected", "CRITICAL")] * 4
    assert _payloads(plain_db, "AnomalyDetected") == []
    assert [t["side"] for t in _decisions(plain_db)["TradeExecuted"]] == ["BUY", "SELL"]
    assert _decisions(noisy_db) == _decisions(plain_db)
    assert (
        signal_kinds(noisy_channel),
        noisy.tracker.snapshot.positions,
    ) == (signal_kinds(plain_channel), plain.tracker.snapshot.positions)
