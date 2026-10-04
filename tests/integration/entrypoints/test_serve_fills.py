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
"""

from __future__ import annotations

import asyncio
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
    run_once,
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
) -> tuple[serve_module.Composition, _SteppingFeed]:
    """Build one boot of the loop over ``db`` with live books."""
    clock = SimulatedClock(start=h4_bar(warm_upto).close_time + _DELIVERY_DELAY)
    books = _LiveBooks(clock)
    books.mid = h4_bar(warm_upto).close
    feed = _SteppingFeed(clock, books, warm=history(warm_upto), live_bars=live(live_from, live_to))
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
