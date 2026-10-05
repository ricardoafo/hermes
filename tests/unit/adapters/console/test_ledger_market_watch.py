"""The ledger read model's market watch (#650).

Ledgers are synthetic and built through the real ledger API (see
``_ledger_builder``); no test reads a real ledger or live data.

Invariants:

- one row per checked (pair, interval) stream, carrying only the newest check
  time, the signal kind of that check and the next due check;
- a signal belongs to the check of its own processing cycle (same correlation
  id), never to a later check of the same pair;
- an empty ledger has nothing to watch;
- reading the view leaves the ledger file byte-for-byte unchanged.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from hermes.adapters.console.ledger_read_model import LedgerReadModel
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
