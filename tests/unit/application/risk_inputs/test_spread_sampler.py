"""Unit tests for the periodic spread sampler (#835).

The depth source is a scripted fake; the store is a real in-memory ledger.
The depth seam is covered against a recorded Binance response in
``tests/unit/adapters/market_data/test_binance_depth_snapshot.py``, and the
sampler inside serve by ``tests/integration/entrypoints/test_serve_risk_inputs.py``.

Invariants under test:

- One round stores one sample per pair with the book's relative spread.
- Each sample's median covers that pair's samples of the hour before it plus
  itself, and nothing older.
- A failed, one-sided or hung book is logged and skipped; the other pairs are
  still sampled and nothing is raised.
- ``run`` samples on its step and ends promptly on shutdown, even mid-round.
"""

from __future__ import annotations

import asyncio
import functools
import sqlite3
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from structlog.testing import capture_logs

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.risk_inputs.spread_sampler import SpreadSampler
from hermes.domain.errors import NetworkError
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_T0 = datetime(2026, 10, 6, 12, tzinfo=UTC)
_FAR = _T0 + timedelta(days=1)


def _sync(
    test: Callable[..., Coroutine[Any, Any, None]],
) -> Callable[..., None]:
    """Run an async test body on a fresh event loop (the suite has no async plugin)."""

    @functools.wraps(test)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


class _Books:
    """Answers a book with ``spreads[pair]`` around 100 at the clock's time."""

    def __init__(self, clock: _Clock, spreads: dict[Pair, str | Exception | None]) -> None:
        self.clock = clock
        self.spreads = spreads
        self.calls: list[Pair] = []
        self.hang = asyncio.Event()

    async def snapshot(self, pair: Pair) -> Orderbook:
        self.calls.append(pair)
        spread = self.spreads[pair]
        if isinstance(spread, Exception):
            raise spread
        if spread == "hang":
            await self.hang.wait()
        half = Decimal(spread or "0") * 50
        bids = (OrderbookLevel(price=100 - half, quantity=Decimal(1)),)
        asks = () if spread is None else (OrderbookLevel(price=100 + half, quantity=Decimal(1)),)
        return Orderbook(pair=pair, bids=bids, asks=asks, snapshot_time=self.clock.now())

    async def aclose(self) -> None:
        """Nothing to release."""


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Yield a migrated in-memory ledger."""
    conn = sqlite3.connect(":memory:")
    try:
        yield SqliteLedger(conn)
    finally:
        conn.close()


def _sampler(ledger: SqliteLedger, books: _Books, clock: _Clock, **kwargs: float) -> SpreadSampler:
    return SpreadSampler(books=books, store=ledger, clock=clock, pairs=(_BTC, _ETH), **kwargs)


@_sync
async def test_one_round_stores_one_sample_per_pair(ledger: SqliteLedger) -> None:
    """Both pairs get a row carrying the book's (ask - bid) / mid."""
    clock = _Clock(_T0)
    books = _Books(clock, {_BTC: "0.0002", _ETH: "0.0004"})
    assert await _sampler(ledger, books, clock).sample_once() == 2
    (btc,) = ledger.spread_samples(_BTC, _T0, _FAR)
    (eth,) = ledger.spread_samples(_ETH, _T0, _FAR)
    assert (btc.spread, btc.median_spread_1h, btc.samples_1h) == (
        Decimal("0.0002"),
        Decimal("0.0002"),
        1,
    )
    assert eth.spread == Decimal("0.0004")


@_sync
async def test_median_covers_the_last_hour_only(ledger: SqliteLedger) -> None:
    """Samples at 0, 30, 61 and 65 minutes: the last median spans 30, 61 and 65 only."""
    clock = _Clock(_T0)
    books = _Books(clock, {_BTC: "0.0010", _ETH: "0.0001"})
    sampler = _sampler(ledger, books, clock)
    for minute, spread in ((0, "0.0010"), (30, "0.0002"), (61, "0.0004"), (65, "0.0003")):
        clock.current = _T0 + timedelta(minutes=minute)
        books.spreads[_BTC] = spread
        await sampler.sample_once()
    last = ledger.spread_samples(_BTC, _T0, _FAR)[-1]
    assert last.samples_1h == 3
    assert last.median_spread_1h == Decimal("0.0003")


@_sync
async def test_failed_and_one_sided_books_are_skipped(ledger: SqliteLedger) -> None:
    """An outage and a one-sided book store nothing, log a warning, raise nothing."""
    clock = _Clock(_T0)
    books = _Books(clock, {_BTC: NetworkError("down"), _ETH: None})
    with capture_logs() as logs:
        assert await _sampler(ledger, books, clock).sample_once() == 0
    assert books.calls == [_BTC, _ETH]
    assert ledger.spread_samples(_BTC, _T0, _FAR) == ()
    assert ledger.spread_samples(_ETH, _T0, _FAR) == ()
    assert [e["event"] for e in logs] == [
        "serve.spread_sample.failed",
        "serve.spread_sample.one_sided_book",
    ]


@_sync
async def test_failure_of_one_pair_still_samples_the_next(ledger: SqliteLedger) -> None:
    """BTC fails, ETH is still stored."""
    clock = _Clock(_T0)
    books = _Books(clock, {_BTC: NetworkError("down"), _ETH: "0.0001"})
    assert await _sampler(ledger, books, clock).sample_once() == 1
    assert len(ledger.spread_samples(_ETH, _T0, _FAR)) == 1


@_sync
async def test_a_hung_book_times_out_and_is_skipped(ledger: SqliteLedger) -> None:
    """A book that never answers is abandoned after the timeout."""
    clock = _Clock(_T0)
    books = _Books(clock, {_BTC: "hang", _ETH: "0.0001"})
    sampler = _sampler(ledger, books, clock, timeout_seconds=0.05)
    with capture_logs() as logs:
        assert await asyncio.wait_for(sampler.sample_once(), timeout=5) == 1
    assert logs[0]["error"] == "TimeoutError"


@_sync
async def test_run_samples_on_its_step_and_stops_on_shutdown(ledger: SqliteLedger) -> None:
    """A short step gives several rounds; shutdown ends the task."""
    clock = _Clock(_T0)
    books = _Books(clock, {_BTC: "0.0001", _ETH: "0.0001"})
    shutdown = asyncio.Event()
    task = asyncio.create_task(_sampler(ledger, books, clock, interval_seconds=0.01).run(shutdown))
    for _ in range(500):
        if len(books.calls) >= 4:
            break
        await asyncio.sleep(0.01)
    shutdown.set()
    await asyncio.wait_for(task, timeout=5)
    assert books.calls[:4] == [_BTC, _ETH, _BTC, _ETH]


@_sync
async def test_shutdown_cuts_a_round_in_flight(ledger: SqliteLedger) -> None:
    """A hung book does not hold shutdown back for the sample timeout."""
    clock = _Clock(_T0)
    books = _Books(clock, {_BTC: "hang", _ETH: "0.0001"})
    shutdown = asyncio.Event()
    sampler = _sampler(ledger, books, clock, interval_seconds=0.01, timeout_seconds=60)
    task = asyncio.create_task(sampler.run(shutdown))
    for _ in range(500):
        if books.calls:
            break
        await asyncio.sleep(0.01)
    shutdown.set()
    await asyncio.wait_for(task, timeout=5)


def test_step_and_timeout_must_be_positive(ledger: SqliteLedger) -> None:
    """A zero step would spin the event loop."""
    clock = _Clock(_T0)
    with pytest.raises(ValueError, match="must be > 0"):
        _sampler(ledger, _Books(clock, {}), clock, interval_seconds=0)
