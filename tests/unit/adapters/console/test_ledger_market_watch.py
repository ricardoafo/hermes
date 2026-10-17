"""The ledger read model's market watch (#650).

Ledgers are synthetic and built through the real ledger API (see
``_ledger_builder``); no test reads a real ledger or live data.

Invariants:

- one row per checked (pair, interval) stream, carrying only the newest check
  time, the signal kind of that check and the next due check;
- a signal belongs to the check of its own processing cycle (same correlation
  id), never to a later check of the same pair;
- an empty ledger has nothing to watch;
- reading the view leaves the ledger file byte-for-byte unchanged;
- the read costs SQLite work linear in the number of bar checks, so the
  console's 5 s poll and every screen switch stay quick as the shadow run
  grows (a per-row lookup of each stream's newest check took about 2 s at
  60 days of 4h and 1d checks on three coins, on the console's UI thread).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

import hermes.adapters.console.ledger_read_model as read_model_module
from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.adapters.ledger.connection import open_ledger
from hermes.application.console.views import WatchedPair
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind
from tests.unit.adapters.console._ledger_builder import BTC, ETH, H4, T0, LedgerBuilder

_D1 = timedelta(days=1)


def _model(path: Path) -> LedgerReadModel:
    return LedgerReadModel.open(path, clock=lambda: T0 + H4 * 2)


@pytest.fixture
def watched(tmp_path: Path) -> Path:
    """Return a ledger with BTC checked on 4h and 1d and ETH on 4h."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    try:
        b.check(T0, signal=SignalKind.ENTRY)
        b.check(T0 + H4)
        b.check(T0 + H4, pair=ETH, signal=SignalKind.EXIT)
        b.check(T0 - timedelta(hours=8), interval=KlineInterval.D1)
    finally:
        b.close()
    return path


def test_one_row_per_stream_with_its_newest_check(watched: Path) -> None:
    """Each stream shows its newest check, that check's signal and the next due check."""
    assert _model(watched).market_watch() == (
        WatchedPair(
            coin="BTC",
            quote="USDT",
            timeframe="1d",
            last_check_at=T0 - timedelta(hours=8),
            signal=None,
            next_check_at=T0 - timedelta(hours=8) + _D1,
        ),
        WatchedPair(
            coin="BTC",
            quote="USDT",
            timeframe="4h",
            last_check_at=T0 + H4,
            signal=None,
            next_check_at=T0 + H4 * 2,
        ),
        WatchedPair(
            coin="ETH",
            quote="USDT",
            timeframe="4h",
            last_check_at=T0 + H4,
            signal="exit",
            next_check_at=T0 + H4 * 2,
        ),
    )


def test_signal_stays_with_the_check_that_produced_it(tmp_path: Path) -> None:
    """An entry on the 08:00 check is not reported for the quiet 12:00 check after it."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    try:
        b.check(T0, pair=BTC, signal=SignalKind.ENTRY)
        b.check(T0 + H4, pair=BTC)
    finally:
        b.close()
    rows = _model(path).market_watch()
    assert [(row.last_check_at, row.signal) for row in rows] == [(T0 + H4, None)]


def test_latest_check_with_a_signal_reports_its_kind(tmp_path: Path) -> None:
    """When the newest check produced an entry, the row says "entry"."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    try:
        b.check(T0)
        b.check(T0 + H4, signal=SignalKind.ENTRY)
    finally:
        b.close()
    assert [row.signal for row in _model(path).market_watch()] == ["entry"]


def test_empty_ledger_watches_nothing(tmp_path: Path) -> None:
    """A freshly migrated ledger with no bar check gives an empty market watch."""
    path = tmp_path / "hermes.sqlite3"
    LedgerBuilder(path).close()
    assert _model(path).market_watch() == ()


def test_reading_the_market_watch_writes_nothing(watched: Path) -> None:
    """The view opens the ledger read-only: the file bytes do not change."""
    before = hashlib.sha256(watched.read_bytes()).hexdigest()
    _model(watched).market_watch()
    assert hashlib.sha256(watched.read_bytes()).hexdigest() == before


def test_unknown_interval_is_a_ledger_error(tmp_path: Path) -> None:
    """A bar check with an interval hermes does not know is reported, not guessed."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    try:
        b.check(T0)
    finally:
        b.close()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE events SET payload_json = json_set(payload_json, '$.interval', '7x') "
            "WHERE event_type = 'BarProcessed'"
        )
    with pytest.raises(LedgerError, match="market watch: unreadable bar check"):
        _model(path).market_watch()


_STEP_GRAIN = 100


def _checked_ledger(path: Path, bars: int) -> Path:
    """Write ``bars`` 4h checks on BTC and ETH, and a 1d check every sixth bar."""
    b = LedgerBuilder(path)
    try:
        for i in range(bars):
            for pair in (BTC, ETH):
                b.check(T0 + H4 * i, pair=pair)
                if i % 6 == 0:
                    b.check(T0 + H4 * i, pair=pair, interval=KlineInterval.D1)
    finally:
        b.close()
    return path


def _read_cost(path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Return the SQLite virtual-machine work of one market-watch read, in units of 100 steps."""
    ticks = [0]

    def counted(ledger: Path) -> sqlite3.Connection:
        conn = open_ledger(ledger, read_only=True)

        def tick() -> int:
            ticks[0] += 1
            return 0

        conn.set_progress_handler(tick, _STEP_GRAIN)
        return conn

    monkeypatch.setattr(read_model_module, "connect_read_only", counted)
    _model(path).market_watch()
    return ticks[0]


def test_read_cost_grows_linearly_with_the_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twice the bar checks costs about twice the work; a per-check lookup would cost four times."""
    small = _read_cost(_checked_ledger(tmp_path / "small.sqlite3", 120), monkeypatch)
    large = _read_cost(_checked_ledger(tmp_path / "large.sqlite3", 240), monkeypatch)
    assert large <= small * 5 // 2, (small, large)
