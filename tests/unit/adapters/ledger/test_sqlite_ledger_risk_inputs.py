"""Unit tests for the recorded RO-01 / RO-02 inputs in the SQLite ledger (#835).

Invariants under test:

- Migration ``20261006T1200_risk_inputs`` is additive: on a ledger migrated
  through the previous file it adds the two tables, keeps every existing row
  and applies once.
- A spread sample and a pair correlation read back exactly.
- One spread sample per (pair, book time) and one correlation per (pairs,
  window, as-of): a cached book or a replayed 1D bar writes nothing.
- The reads honour ``start <= t < end``, filter by pair and sort oldest first.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import (
    DESTRUCTIVE_MARKER,
    MIGRATIONS_DIR,
    apply_migrations,
)
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.portfolio.correlation import PairCorrelation
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_MIGRATION = "20261006T1200_risk_inputs.sql"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
_T0 = datetime(2026, 10, 6, 12, tzinfo=UTC)
_STEP = timedelta(minutes=5)


def _sample(i: int, *, pair: Pair = _BTC, spread: str = "0.00012345") -> SpreadSample:
    at = _T0 + _STEP * i
    return SpreadSample(
        pair=pair,
        sampled_at=at - timedelta(milliseconds=150),
        book_time=at,
        spread=Decimal(spread),
        median_spread_1h=Decimal("0.000100005"),
        samples_1h=i + 1,
    )


def _correlation(
    day: int, *, a: Pair = _BTC, b: Pair = _ETH, value: float = 0.8123456789
) -> PairCorrelation:
    return PairCorrelation(
        pair_a=a,
        pair_b=b,
        window_days=30,
        as_of=_T0 + timedelta(days=day) - timedelta(milliseconds=1),
        correlation=value,
        sample_count=29,
    )


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Yield a migrated in-memory ledger."""
    conn = sqlite3.connect(":memory:")
    try:
        yield SqliteLedger(conn)
    finally:
        conn.close()


def test_migration_is_additive_and_keeps_existing_rows(tmp_path: Path) -> None:
    """Applied after the previous file, it adds both tables and loses no row."""
    pre, upto = tmp_path / "pre", tmp_path / "upto"
    pre.mkdir()
    upto.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if path.name < _MIGRATION:
            shutil.copy(path, pre / path.name)
        if path.name <= _MIGRATION:
            shutil.copy(path, upto / path.name)
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, pre)
    conn.execute(
        "INSERT INTO live_bars VALUES ('BTC', 'USDT', '1d', '2026-10-05T00:00:00+00:00', "
        "'2026-10-05T23:59:59.999000+00:00', '1', '2', '0.5', '1.5', '10', "
        "'2026-10-06T00:00:05+00:00')"
    )
    conn.commit()
    assert apply_migrations(conn, upto) == (_MIGRATION,)
    assert apply_migrations(conn, upto) == ()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"spread_samples", "pair_correlations"} <= tables
    assert conn.execute("SELECT COUNT(*) FROM live_bars").fetchone() == (1,)
    conn.close()


def test_migration_is_not_marked_destructive() -> None:
    """The runner applies it on boot, without the operator's migrate step."""
    text = (MIGRATIONS_DIR / _MIGRATION).read_text(encoding="utf-8").lstrip()
    assert not text.startswith(DESTRUCTIVE_MARKER)


def test_spread_sample_reads_back_exactly(ledger: SqliteLedger) -> None:
    """Every field, Decimal digits included, survives the round trip."""
    sample = _sample(0)
    assert ledger.record_spread_sample(sample) is True
    assert ledger.spread_samples(_BTC, _T0, _T0 + _STEP) == (sample,)


def test_same_book_is_stored_once(ledger: SqliteLedger) -> None:
    """A cached book sampled twice keeps the first row."""
    assert ledger.record_spread_sample(_sample(0)) is True
    assert ledger.record_spread_sample(_sample(0, spread="0.9")) is False
    (stored,) = ledger.spread_samples(_BTC, _T0, _T0 + _STEP)
    assert stored.spread == Decimal("0.00012345")


def test_spread_read_is_half_open_per_pair_and_oldest_first(ledger: SqliteLedger) -> None:
    """``start <= book_time < end``, one pair only, ascending."""
    for i in (2, 0, 1, 3):
        ledger.record_spread_sample(_sample(i))
    ledger.record_spread_sample(_sample(1, pair=_ETH))
    got = ledger.spread_samples(_BTC, _T0 + _STEP, _T0 + _STEP * 3)
    assert [s.book_time for s in got] == [_T0 + _STEP, _T0 + _STEP * 2]
    assert {s.pair for s in got} == {_BTC}


def test_correlation_reads_back_exactly(ledger: SqliteLedger) -> None:
    """The float value and the window survive the round trip."""
    value = _correlation(0)
    assert ledger.record_pair_correlation(value) is True
    assert ledger.pair_correlations(_T0 - timedelta(days=1), _T0 + timedelta(days=1)) == (value,)


def test_replayed_correlation_is_stored_once(ledger: SqliteLedger) -> None:
    """A 1D bar replayed after a restart keeps the first value."""
    assert ledger.record_pair_correlation(_correlation(0)) is True
    assert ledger.record_pair_correlation(_correlation(0, value=-0.5)) is False
    (stored,) = ledger.pair_correlations(_T0 - timedelta(days=1), _T0 + timedelta(days=1))
    assert stored.correlation == pytest.approx(0.8123456789)


def test_correlation_read_is_half_open_and_ordered(ledger: SqliteLedger) -> None:
    """``start <= as_of < end``, by as-of then pair names."""
    for record in (_correlation(1), _correlation(1, b=_SOL), _correlation(0), _correlation(2)):
        ledger.record_pair_correlation(record)
    got = ledger.pair_correlations(_T0, _T0 + timedelta(days=1))
    assert [(c.as_of, str(c.pair_b)) for c in got] == [
        (_correlation(1).as_of, "ETH/USDT"),
        (_correlation(1).as_of, "SOL/USDT"),
    ]


def test_unreadable_row_raises_ledger_error(ledger: SqliteLedger) -> None:
    """A corrupted spread fails loudly instead of reading back as a number."""
    ledger.record_spread_sample(_sample(0))
    ledger._conn.execute("UPDATE spread_samples SET spread = 'not-a-number'")
    with pytest.raises(LedgerError, match="unreadable"):
        ledger.spread_samples(_BTC, _T0, _T0 + _STEP)
