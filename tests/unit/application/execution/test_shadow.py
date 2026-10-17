"""Tests for ShadowExecutor, the live-shadow side of the shared fill kernel.

Invariants (change 353-fill-kernel-in-serve D-1..D-5):

- A fresh bar fills on the live book; the trade is recorded with the
  strategy id and a quality row measures its drift against the §6.2 price.
- A failed snapshot, or a bar older than the live window, falls back to the
  §6.2 book and says so; an entry on that book is refused by RO-04, an exit
  fills at the §6.2 price stamped at the signal time.
- A signal whose pair and side already traded since the signal time is never
  filled twice (restart idempotency).
- RO-07 sees the BTC/USDT 1d log-return of the bars observed, and RO-03 sees
  the last recorded fill slippage, including one recorded before a restart.
- Under v2's close-based pricing a stop exit books close x 0.9995 in serve and
  in the backtest replay on the same bar, and a live v2 exit's drift is
  measured against that same price (v2-strategy-config R-1).

Collaborators are real (kernel, router, walk fill model, SQLite ledger,
trade recorder); only the depth source, the FX provider and the clock are
scripted. The depth seam is covered by
``tests/unit/adapters/market_data/test_binance_depth_snapshot.py``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import anyio
import pytest

from hermes.adapters.clock import SimulatedClock
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.backtest.replay import replay
from hermes.application.execution.kernel import Declined, Executed, FillKernel, Halted
from hermes.application.execution.prereg_book import ExecutionModel, ExitPricing
from hermes.application.execution.shadow import (
    SLIPPAGE_WINDOW,
    AlreadyFilled,
    ShadowExecution,
    ShadowExecutor,
    shadow_risk_engine,
)
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.errors import NetworkError
from hermes.domain.events.fill_quality_recorded import BookSource, FillQualityRecorded
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.pair import Pair
from tests.support.fill_kernel import BTC_USDT, SIZING, STRATEGY, fees, kill_evaluator, seed
from tests.support.fill_kernel import kernel as backtest_kernel
from tests.unit.application.ledger.fake_fx import FakeFx

_OPEN = datetime(2026, 10, 4, 0, tzinfo=UTC)
_CLOSE = close_time_of(_OPEN, KlineInterval.H4)
_NOW = _CLOSE + timedelta(seconds=6)


@dataclass
class _Books:
    """Scripted depth source: returns ``book`` (restamped at the clock) or raises."""

    clock: SimulatedClock
    book: Orderbook | None = None
    calls: list[Pair] = field(default_factory=list)

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return the scripted book stamped now, or fail like an outage."""
        self.calls.append(pair)
        if self.book is None:
            raise NetworkError("depth endpoint unreachable")
        return Orderbook(
            pair=pair, bids=self.book.bids, asks=self.book.asks, snapshot_time=self.clock.now()
        )

    async def aclose(self) -> None:
        """Nothing to release."""


def _live_book(bid: str, ask: str) -> Orderbook:
    """Return a deep two-level book around ``bid``/``ask``."""
    return Orderbook(
        pair=BTC_USDT,
        bids=(
            OrderbookLevel(price=Decimal(bid), quantity=Decimal("5")),
            OrderbookLevel(price=Decimal(bid) - 1, quantity=Decimal("50")),
        ),
        asks=(
            OrderbookLevel(price=Decimal(ask), quantity=Decimal("5")),
            OrderbookLevel(price=Decimal(ask) + 1, quantity=Decimal("50")),
        ),
        snapshot_time=_NOW,
    )


def _bar(close: str, *, open_time: datetime = _OPEN) -> Bar:
    """Return the 4h BTC/USDT signal bar."""
    price = Decimal(close)
    return Bar(
        pair=BTC_USDT,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=price,
        high=price + 10,
        low=price - 10,
        close=price,
        volume=Decimal("500"),
        close_time=close_time_of(open_time, KlineInterval.H4),
    )


def _daily(close: str, day: int) -> Bar:
    """Return a BTC/USDT 1d bar ``day`` days before the signal bar's day."""
    open_time = _OPEN - timedelta(days=day)
    price = Decimal(close)
    return Bar(
        pair=BTC_USDT,
        interval=KlineInterval.D1,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
        close_time=close_time_of(open_time, KlineInterval.D1),
    )


def _entry(bar: Bar) -> StrategySignal:
    return StrategySignal(
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=bar.close,
        stop_price=bar.close - 100,
        atr_context=Decimal("50"),
        signal_time=bar.close_time,
    )


def _exit(bar: Bar, stop: str) -> StrategySignal:
    return StrategySignal(
        kind=SignalKind.EXIT,
        side=SignalSide.LONG,
        entry_price=None,
        stop_price=Decimal(stop),
        atr_context=Decimal("50"),
        signal_time=bar.close_time,
        exit_reason="chandelier",
    )


@dataclass
class _Rig:
    executor: ShadowExecutor
    books: _Books
    ledger: SqliteLedger
    clock: SimulatedClock
    kernel: FillKernel

    def run(self, signal: StrategySignal, bar: Bar, cid: str = "c1") -> ShadowExecution:
        async def _go() -> ShadowExecution:
            return await self.executor.execute(signal, bar, correlation_id=cid)

        return anyio.run(_go)


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    """Return an in-memory SQLite connection, closed afterwards."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


def _rig(
    connection: sqlite3.Connection,
    *,
    book: Orderbook | None = None,
    daily: tuple[str, str] | None = ("84000", "84500"),
    not_before: datetime | None = None,
    model: ExecutionModel | None = None,
) -> _Rig:
    """Build an executor over real collaborators, with two BTC 1d bars observed."""
    clock = SimulatedClock(start=_NOW)
    ledger = SqliteLedger(connection)
    fill_model = OrderbookWalkFillModel()
    kernel = FillKernel(
        fill_model=fill_model,
        risk_engine=shadow_risk_engine(),
        kill_evaluator=kill_evaluator(),
        tracker=PortfolioTracker.rebuilt(seed(), ledger.trade_events(STRATEGY)).unwrap(),
        strategy_id=STRATEGY,
        sizing=SIZING,
    )
    books = _Books(clock=clock, book=book)
    executor = ShadowExecutor(
        kernel=kernel,
        fill_model=fill_model,
        books=books,
        recorder=TradeRecorder(ledger, EurPricer(FakeFx(down=True))),
        ledger=ledger,
        clock=clock,
        fees_at=lambda _at, _pair: fees(),
        strategy_id=STRATEGY,
        model=model,
        not_before=not_before,
    )
    if daily is not None:
        executor.observe(_daily(daily[0], 2))
        executor.observe(_daily(daily[1], 1))
    return _Rig(executor=executor, books=books, ledger=ledger, clock=clock, kernel=kernel)


_PRICE_QUANTUM = Decimal("1E-12")


def _price(value: Decimal | None) -> Decimal | None:
    """Cut a walked price to 12 decimals.

    The pre-registered sizing gives quantities like ``250 / 85000`` that do not
    terminate, so a one-level walk's average price differs from the level by a
    rounding residue far below a cent.
    """
    return None if value is None else value.quantize(_PRICE_QUANTUM)


def _executed(result: ShadowExecution) -> Executed:
    assert isinstance(result.outcome, Executed), f"expected Executed, got {result.outcome!r}"
    return result.outcome


def _declined(result: ShadowExecution) -> Declined:
    assert isinstance(result.outcome, Declined), f"expected Declined, got {result.outcome!r}"
    return result.outcome


# SEV-1: real-money guard (AGENTS.md prohibition 7) — a shadow fill is a ledger
# record from the simulated walk, stamped with its strategy, never a venue order.
def test_fresh_entry_fills_on_the_live_ask_and_is_recorded_with_its_strategy(
    connection: sqlite3.Connection,
) -> None:
    """The fill takes the live touch and lands in the ledger as a strategy trade."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    out = _executed(rig.run(_entry(bar), bar))

    trades = rig.ledger.trade_events(STRATEGY)
    assert (_price(out.fill.avg_price), len(trades), trades[0].strategy_id, trades[0].side) == (
        Decimal("85001.000000000000"),
        1,
        STRATEGY,
        TradeSide.BUY,
    )


def test_live_fill_is_stamped_with_the_snapshot_time(connection: sqlite3.Connection) -> None:
    """A live fill happened when the book was read, not at the bar close."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    assert _executed(rig.run(_entry(bar), bar)).fill.timestamp == _NOW


def test_a_fill_is_never_stamped_before_the_capital_seed(
    connection: sqlite3.Connection,
) -> None:
    """A book read before the seed time is stamped at the seed: the seed lot pays for it."""
    seeded_at = _NOW + timedelta(seconds=1)
    rig = _rig(connection, book=_live_book("84999", "85001"), not_before=seeded_at)
    bar = _bar("85000")

    assert _executed(rig.run(_entry(bar), bar)).fill.timestamp == seeded_at


def test_live_fill_records_its_drift_against_the_prereg_entry_price(
    connection: sqlite3.Connection,
) -> None:
    """Model price is close x 1.0005 (§6.2); 85001 vs 85042.5 is a favourable drift."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    rig.run(_entry(bar), bar)

    (quality,) = rig.ledger.fill_quality_events(STRATEGY)
    model = Decimal("85000") * Decimal("1.0005")
    assert (quality.book_source, _price(quality.model_price), _price(quality.drift_bps)) == (
        BookSource.LIVE,
        _price(model),
        _price((Decimal("85001") - model) / model * Decimal("10000")),
    )


def test_quality_row_records_the_signal_bar_close_as_arrival_price(
    connection: sqlite3.Connection,
) -> None:
    """Arrival price is the signal bar's close, not the fill or the book price."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    rig.run(_entry(bar), bar)

    (quality,) = rig.ledger.fill_quality_events(STRATEGY)
    assert quality.arrival_price == Decimal("85000")


def test_live_fill_records_the_latency_from_signal_close_to_the_snapshot(
    connection: sqlite3.Connection,
) -> None:
    """The live fill is stamped 6 s after the bar close, so delta_ms is 6000."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    rig.run(_entry(bar), bar)

    (quality,) = rig.ledger.fill_quality_events(STRATEGY)
    assert quality.delta_ms == 6000


def test_synthetic_fill_at_the_signal_time_records_zero_latency(
    connection: sqlite3.Connection,
) -> None:
    """A fill on the §6.2 book is stamped at the signal time: no latency."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    entry_bar = _bar("85000")
    rig.run(_entry(entry_bar), entry_bar)
    exit_bar = _bar("84000", open_time=_OPEN + timedelta(hours=4))
    rig.clock.set(exit_bar.close_time + timedelta(hours=3))

    rig.run(_exit(exit_bar, "84100"), exit_bar, cid="c2")

    synthetic = rig.ledger.fill_quality_events(STRATEGY)[-1]
    assert (synthetic.book_source, synthetic.delta_ms, synthetic.arrival_price) == (
        BookSource.SYNTHETIC,
        0,
        Decimal("84000"),
    )


def test_quality_row_shares_the_trade_correlation_id(connection: sqlite3.Connection) -> None:
    """The gate can join a quality row to its trade."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    rig.run(_entry(bar), bar, cid="cid-1")

    (quality,) = rig.ledger.fill_quality_events(STRATEGY)
    assert (quality.correlation_id, rig.ledger.trade_events(STRATEGY)[0].correlation_id) == (
        "cid-1",
        "cid-1",
    )


def test_failed_snapshot_falls_back_and_ro04_refuses_the_entry(
    connection: sqlite3.Connection,
) -> None:
    """No live book: the §6.2 book is seconds old, so the stale-book rule halts the entry."""
    rig = _rig(connection, book=None)
    bar = _bar("85000")

    result = rig.run(_entry(bar), bar)

    declined = _declined(result)
    assert (
        declined.rule,
        result.book_source,
        result.snapshot_error,
        rig.ledger.trade_events(STRATEGY),
    ) == (
        "HALT",
        BookSource.SYNTHETIC,
        str(NetworkError("depth endpoint unreachable")),
        (),
    )


def test_stale_bar_exit_fills_on_the_prereg_book_without_asking_the_venue(
    connection: sqlite3.Connection,
) -> None:
    """A catch-up exit sells at stop x 0.9985 (§6.2), stamped at the signal time."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    entry_bar = _bar("85000")
    _executed(rig.run(_entry(entry_bar), entry_bar, cid="a"))
    exit_bar = _bar("84000", open_time=_OPEN + timedelta(hours=4))
    rig.clock.set(exit_bar.close_time + timedelta(hours=3))
    calls_before = len(rig.books.calls)

    out = _executed(rig.run(_exit(exit_bar, "83995"), exit_bar, cid="b"))

    assert (out.fill.avg_price, out.fill.timestamp, len(rig.books.calls)) == (
        Decimal("83995") * Decimal("0.9985"),
        exit_bar.close_time,
        calls_before,
    )


@dataclass
class _Scripted:
    """A strategy that emits a fixed signal on given bars; the backtest's stand-in."""

    signals: dict[datetime, StrategySignal]

    def on_bar(self, bar: Bar, portfolio_state: object) -> StrategySignal | None:
        """Return the signal scripted for this bar's close, if any."""
        return self.signals.get(bar.close_time)

    def seed(self, bars: object, open_position: object = None) -> None:
        """Nothing to warm up."""

    def restore_position(self, open_position: object) -> None:
        """Nothing to restore."""


def test_a_gap_stop_exit_books_the_same_price_in_the_backtest_and_serve(
    connection: sqlite3.Connection,
) -> None:
    """ADR-011 parity (#785): a bar opening under the stop sells from its open in both paths.

    The exit bar opens at 84000 under the 84100 stop; the stale serve exit and
    the backtest replay both book 84000 x 0.9985, never the stop.
    """
    entry_bar = _bar("85000")
    exit_bar = _bar("84000", open_time=_OPEN + timedelta(hours=4))
    entry, stop_exit = _entry(entry_bar), _exit(exit_bar, "84100")
    rig = _rig(connection, book=_live_book("84999", "85001"))
    _executed(rig.run(entry, entry_bar, cid="a"))
    rig.clock.set(exit_bar.close_time + timedelta(hours=3))
    serve_price = _executed(rig.run(stop_exit, exit_bar, cid="b")).fill.avg_price

    ids = iter(("c", "d"))
    run = replay(
        [entry_bar, exit_bar],
        strategy=_Scripted({entry_bar.close_time: entry, exit_bar.close_time: stop_exit}),
        kernel=backtest_kernel(),
        window_start=entry_bar.close_time,
        window_end=exit_bar.close_time,
        fees_at=lambda _at, _pair: fees(),
        correlation_id=lambda: next(ids),
    )

    assert (_price(serve_price), _price(run.fills[-1].avg_price)) == (
        Decimal("84000") * Decimal("0.9985"),
        Decimal("84000") * Decimal("0.9985"),
    )


_V2_MODEL = ExecutionModel(exit_pricing=ExitPricing.BAR_CLOSE)


def _crash_bar() -> Bar:
    """Return the 4h bar after ``_bar``: opens at 84500 over the 84100 stop, closes at 83200."""
    open_time = _OPEN + timedelta(hours=4)
    return Bar(
        pair=BTC_USDT,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=Decimal("84500"),
        high=Decimal("84510"),
        low=Decimal("83000"),
        close=Decimal("83200"),
        volume=Decimal("500"),
        close_time=close_time_of(open_time, KlineInterval.H4),
    )


def test_a_v2_stop_exit_books_the_bar_close_in_the_backtest_and_serve(
    connection: sqlite3.Connection,
) -> None:
    """ADR-011 parity, v2 §5.3/§6.2: both paths sell the stop exit at 83200 x 0.9995.

    The bar opens over the 84100 stop and closes 900 under it; v1 would book the
    stop, v2 books the close in the stale serve exit and in the replay alike.
    """
    entry_bar, exit_bar = _bar("85000"), _crash_bar()
    entry, stop_exit = _entry(entry_bar), _exit(exit_bar, "84100")
    rig = _rig(connection, book=_live_book("84999", "85001"), model=_V2_MODEL)
    _executed(rig.run(entry, entry_bar, cid="a"))
    rig.clock.set(exit_bar.close_time + timedelta(hours=3))
    serve_price = _executed(rig.run(stop_exit, exit_bar, cid="b")).fill.avg_price

    ids = iter(("c", "d"))
    run = replay(
        [entry_bar, exit_bar],
        strategy=_Scripted({entry_bar.close_time: entry, exit_bar.close_time: stop_exit}),
        kernel=backtest_kernel(),
        window_start=entry_bar.close_time,
        window_end=exit_bar.close_time,
        fees_at=lambda _at, _pair: fees(),
        correlation_id=lambda: next(ids),
        model=_V2_MODEL,
    )

    assert (_price(serve_price), _price(run.fills[-1].avg_price)) == (
        Decimal("83200") * Decimal("0.9995"),
        Decimal("83200") * Decimal("0.9995"),
    )


def test_a_live_v2_stop_exit_measures_its_drift_against_the_bar_close(
    connection: sqlite3.Connection,
) -> None:
    """v2 §7: a fresh exit fills on the live bid 83150; its model price is 83200 x 0.9995."""
    entry_bar, exit_bar = _bar("85000"), _crash_bar()
    rig = _rig(connection, book=_live_book("84999", "85001"), model=_V2_MODEL)
    _executed(rig.run(_entry(entry_bar), entry_bar, cid="a"))
    rig.books.book = _live_book("83150", "83152")
    rig.clock.set(exit_bar.close_time + timedelta(seconds=6))

    out = rig.run(_exit(exit_bar, "84100"), exit_bar, cid="b")

    assert out.quality is not None
    assert (out.quality.book_source, _price(out.quality.model_price)) == (
        BookSource.LIVE,
        _price(Decimal("83200") * Decimal("0.9995")),
    )


def test_exit_sells_the_held_quantity_and_leaves_the_pair_flat(
    connection: sqlite3.Connection,
) -> None:
    """The live exit closes exactly what the entry bought."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    entry_bar = _bar("85000")
    bought = _executed(rig.run(_entry(entry_bar), entry_bar, cid="a")).fill.executed_qty
    exit_bar = _bar("84000", open_time=_OPEN + timedelta(hours=4))
    rig.clock.set(exit_bar.close_time + timedelta(seconds=6))

    sold = _executed(rig.run(_exit(exit_bar, "84100"), exit_bar, cid="b")).fill.executed_qty

    assert (sold, rig.kernel.tracker.snapshot.positions) == (bought, {})


def test_signal_already_filled_is_not_filled_again(connection: sqlite3.Connection) -> None:
    """Re-executing the same entry (a crash before the cursor row) adds no trade."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")
    _executed(rig.run(_entry(bar), bar, cid="a"))

    again = rig.run(_entry(bar), bar, cid="b")

    assert (isinstance(again.outcome, AlreadyFilled), len(rig.ledger.trade_events(STRATEGY))) == (
        True,
        1,
    )


def test_restarted_executor_does_not_refill_a_recorded_signal(
    connection: sqlite3.Connection,
) -> None:
    """A new executor over the same ledger (a restart) also sees the recorded trade."""
    first = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")
    _executed(first.run(_entry(bar), bar, cid="a"))
    second = _rig(connection, book=_live_book("84999", "85001"))

    again = second.run(_entry(bar), bar, cid="b")

    assert (
        isinstance(again.outcome, AlreadyFilled),
        len(second.ledger.trade_events(STRATEGY)),
    ) == (
        True,
        1,
    )


def test_without_two_btc_daily_closes_ro07_halts_the_entry(
    connection: sqlite3.Connection,
) -> None:
    """No macro return yet: RO-07 refuses conservatively."""
    rig = _rig(connection, book=_live_book("84999", "85001"), daily=None)
    bar = _bar("85000")

    assert _declined(rig.run(_entry(bar), bar)).rule == "HALT"


def test_a_btc_daily_crash_beyond_ten_percent_halts_the_entry(
    connection: sqlite3.Connection,
) -> None:
    """ln(75000/84000) is about -11%: RO-07 halts new entries."""
    rig = _rig(connection, book=_live_book("84999", "85001"), daily=("84000", "75000"))
    bar = _bar("85000")

    assert _declined(rig.run(_entry(bar), bar)).rule == "HALT"


def _slipped_fill(
    connection: sqlite3.Connection, *, before_close: timedelta, slippage: str
) -> None:
    """Record the quality row of an earlier BTC sell that slipped by ``slippage``."""
    at = _CLOSE - before_close
    SqliteLedger(connection).append_event(
        FillQualityRecorded(
            occurred_at=at,
            correlation_id=f"old-{before_close}",
            strategy_id=STRATEGY,
            pair=BTC_USDT,
            side=TradeSide.SELL,
            qty=Decimal("0.001"),
            book_source=BookSource.LIVE,
            book_time=at,
            fill_price=Decimal(100) - Decimal(slippage) * 100,
            model_price=None,
            drift_bps=None,
            book_mid=Decimal("100"),
            slippage=Decimal(slippage),
        )
    )


def test_a_slippage_recorded_before_a_restart_feeds_ro03(connection: sqlite3.Connection) -> None:
    """A fill an hour ago that slipped 0.6% (limit 0.5%) halts the next entry after a reboot."""
    _slipped_fill(connection, before_close=timedelta(hours=1), slippage="0.006")
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    assert _declined(rig.run(_entry(bar), bar)).rule == "HALT"


# SEV-1: no rubric row matches; RO-03 must not halt entries for good after one slipping exit
def test_a_slipping_fill_older_than_the_window_no_longer_halts_entries(
    connection: sqlite3.Connection,
) -> None:
    """#857: a 0.6% slip 24 hours before the bar close no longer halts; the entry fills."""
    _slipped_fill(connection, before_close=SLIPPAGE_WINDOW, slippage="0.006")
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    rig.run(_entry(bar), bar)

    bought = [(t.pair, t.side) for t in rig.ledger.trade_events(STRATEGY)]
    assert bought == [(BTC_USDT, TradeSide.BUY)]


# SEV-1: no rubric row matches; the RO-03 cumulative limit must reach the check
def test_fills_inside_the_window_that_slip_over_2_percent_together_halt_entries(
    connection: sqlite3.Connection,
) -> None:
    """#857: five 0.45% slips in the last day, each under the single limit, sum past 2% and halt."""
    for hours in range(1, 6):
        _slipped_fill(connection, before_close=timedelta(hours=hours), slippage="0.0045")
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    assert _declined(rig.run(_entry(bar), bar)).reason.startswith("RO-03: cumulative slippage")


def test_slips_older_than_the_window_do_not_count_toward_the_cumulative_limit(
    connection: sqlite3.Connection,
) -> None:
    """#857: the same five slips a day and more ago no longer count, so the entry fills."""
    for hours in range(1, 6):
        _slipped_fill(
            connection, before_close=SLIPPAGE_WINDOW + timedelta(hours=hours), slippage="0.0045"
        )
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000")

    rig.run(_entry(bar), bar)

    bought = [(t.pair, t.side) for t in rig.ledger.trade_events(STRATEGY)]
    assert bought == [(BTC_USDT, TradeSide.BUY)]


def test_clock_behind_the_bar_close_records_no_latency_and_still_fills(
    connection: sqlite3.Connection,
) -> None:
    """Record-only (#30): a negative latency is stored as None and never blocks the fill."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    bar = _bar("85000", open_time=_OPEN + timedelta(hours=4))

    rig.run(_entry(bar), bar)

    (quality,) = rig.ledger.fill_quality_events(STRATEGY)
    assert (quality.delta_ms, quality.arrival_price) == (None, Decimal("85000"))


# SEV-1: S6 a held shadow kill that lets entries through keeps a dead strategy trading (#796)
def test_a_held_shadow_kill_refuses_entries_and_still_fills_the_exit(
    connection: sqlite3.Connection,
) -> None:
    """After ``hold_kill`` the next entry is Halted under that rule; the open position exits."""
    rig = _rig(connection, book=_live_book("84999", "85001"))
    entry_bar = _bar("85000")
    _executed(rig.run(_entry(entry_bar), entry_bar, cid="a"))
    rig.executor.hold_kill("sa9_repeat")
    exit_bar = _bar("84000", open_time=_OPEN + timedelta(hours=4))
    rig.clock.set(exit_bar.close_time + timedelta(seconds=5))

    exited = rig.run(_exit(exit_bar, "84100"), exit_bar, cid="b").outcome
    refused = rig.run(_entry(exit_bar), exit_bar, cid="c").outcome

    assert isinstance(exited, Executed)
    assert isinstance(refused, Halted)
    assert (rig.executor.killed_by, refused.breaker.decision.rule) == ("sa9_repeat", "sa9_repeat")
