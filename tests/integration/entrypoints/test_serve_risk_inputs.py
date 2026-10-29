"""Integration tests: serve records the RO-01 / RO-02 inputs beside the bar loop (#835).

Everything from the bar feed to the SQLite ledger file is real (bar loop,
TaskGroup, spread sampler, correlation recorder, ledger) except the seams
the other serve tests fake: the scripted feed, a fixed clock and a depth
source. The depth seam is covered against a recorded Binance response in
``tests/unit/adapters/market_data/test_binance_depth_snapshot.py``. No test
reaches the network.

Invariants:

- With a short sampling step, serve stores spread samples for every pair
  while it handles bars, and still handles every bar.
- A depth source that always fails stores no sample, logs a warning, and
  neither stops serve (exit 0) nor keeps a bar from being processed.
- Completed 1D bars of two pairs store their correlation, keyed on the bar's
  close time, with the number of daily returns it covers; the journal gets
  the count only.
"""

import dataclasses
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from hermes.application.risk_inputs import spread_sampler
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import serve as serve_module
from tests.support.serve_loop import (
    BTC,
    ETH,
    FakeFeed,
    UnavailableBooks,
    build,
    d1_bar,
    fresh_strategy,
    history,
    live,
    run_once,
)

pytestmark = pytest.mark.integration

_STEP = 0.02


@pytest.fixture(autouse=True)
def _uncached_serve_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give serve and the sampler loggers ``capture_logs`` can see."""
    monkeypatch.setattr(serve_module, "_log", structlog.get_logger(serve_module.__name__))
    monkeypatch.setattr(spread_sampler, "_log", structlog.get_logger(spread_sampler.__name__))
    cached = structlog.get_config()["cache_logger_on_first_use"]
    structlog.configure(cache_logger_on_first_use=False)
    yield
    structlog.configure(cache_logger_on_first_use=cached)


class _TickingBooks:
    """Depth source answering a 2-unit-wide book, stamped one second apart per call."""

    def __init__(self) -> None:
        """Start the stamp clock."""
        self.calls = 0

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return a fresh book around 1000."""
        self.calls += 1
        return Orderbook(
            pair=pair,
            bids=(OrderbookLevel(price=Decimal(999), quantity=Decimal(10)),),
            asks=(OrderbookLevel(price=Decimal(1001), quantity=Decimal(10)),),
            snapshot_time=datetime(2026, 10, 6, tzinfo=UTC) + timedelta(seconds=self.calls),
        )

    async def aclose(self) -> None:
        """Nothing to release."""


def _rows(db_path: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _all_live_bars_processed(db_path: Path) -> bool:
    """Every scripted live 4h bar was handled (serve stores each handled bar)."""
    stored = _rows(db_path, "SELECT open_time FROM live_bars WHERE pair_base = 'BTC'")
    return {b.open_time.isoformat() for b in live(11, 13)} <= {r[0] for r in stored}


def _two_pairs(composition: serve_module.Composition) -> serve_module.Composition:
    return dataclasses.replace(
        composition,
        strategies={BTC: fresh_strategy(), ETH: fresh_strategy()},
        pairs=(BTC, ETH),
        spread_sample_seconds=_STEP,
    )


def test_sampler_stores_samples_for_every_pair_beside_the_bar_loop(tmp_path: Path) -> None:
    """Samples land for BTC and ETH while all live bars are still processed."""
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(10) + history(10, pair=ETH), live_bars=live(11, 13))
    books = _TickingBooks()
    composition = _two_pairs(build(db, feed, datetime(2024, 1, 20, tzinfo=UTC), books=books))

    def sampled_both() -> bool:
        pairs = _rows(db, "SELECT DISTINCT pair_base FROM spread_samples")
        return {p[0] for p in pairs} == {"BTC", "ETH"}

    assert run_once(composition, feed, until=sampled_both) == 0
    assert sampled_both()
    assert _all_live_bars_processed(db)
    spreads = {r[0] for r in _rows(db, "SELECT spread FROM spread_samples")}
    assert spreads == {"0.002"}


def test_failing_depth_source_never_stops_serve(tmp_path: Path) -> None:
    """Every sample fails: no row, a warning, exit 0 and every bar processed."""
    db = tmp_path / "ledger.sqlite3"
    feed = FakeFeed(warm=history(10), live_bars=live(11, 13))
    books = UnavailableBooks()
    composition = dataclasses.replace(
        build(db, feed, datetime(2024, 1, 20, tzinfo=UTC), books=books),
        spread_sample_seconds=_STEP,
    )
    with capture_logs() as logs:
        code = run_once(composition, feed, until=lambda: books.calls >= 3)
    assert code == 0
    assert _all_live_bars_processed(db)
    assert _rows(db, "SELECT COUNT(*) FROM spread_samples") == [(0,)]
    failed = [e for e in logs if e["event"] == "serve.spread_sample.failed"]
    assert failed
    assert failed[0]["error"] == "NetworkError"
    assert not any(e["event"] == "serve.run.failed" for e in logs)


def test_completed_1d_bars_store_pair_correlations(tmp_path: Path) -> None:
    """Days 12-15 of BTC and ETH give values over 2 and 3 returns at each day's close."""
    db = tmp_path / "ledger.sqlite3"
    days = [d1_bar(i, pair) for i in range(12, 16) for pair in (BTC, ETH)]
    feed = FakeFeed(warm=history(10) + history(10, pair=ETH), live_bars=days)
    composition = dataclasses.replace(
        _two_pairs(build(db, feed, datetime(2024, 1, 20, tzinfo=UTC))),
        spread_sample_seconds=3600,
    )
    with capture_logs() as logs:
        assert run_once(composition, feed) == 0
    rows = _rows(
        db,
        "SELECT pair_a, pair_b, window_days, as_of, correlation, sample_count "
        "FROM pair_correlations ORDER BY as_of",
    )
    assert [(r[0], r[1], r[2], r[5]) for r in rows] == [
        ("BTC/USDT", "ETH/USDT", 30, 2),
        ("BTC/USDT", "ETH/USDT", 30, 3),
    ]
    assert [r[3] for r in rows] == [
        d1_bar(14).close_time.isoformat(),
        d1_bar(15).close_time.isoformat(),
    ]
    # Identical closes in both pairs: perfectly correlated returns.
    assert all(r[4] == pytest.approx(1.0) for r in rows)
    recorded = [e for e in logs if e["event"] == "serve.correlations.recorded"]
    assert [e["count"] for e in recorded] == [1, 1]
    assert all("correlation" not in e for e in recorded)
