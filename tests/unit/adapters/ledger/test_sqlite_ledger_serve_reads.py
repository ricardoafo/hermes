"""Tests for the ledger reads the serve loop relies on.

Invariants protected here:

- ``last_processed_close`` returns the newest ``BarProcessed`` close time of
  exactly one ``(pair, interval)`` stream, never another stream's;
- ``latest_signal_event`` returns the newest ``SignalEmitted`` of one
  strategy and pair at or before a bound, rehydrated field for field;
- ``has_signal_event`` matches on strategy, pair, kind and time only.

All run against SQLite ``:memory:``.

Reference: 309-serve-bar-loop change.md D-6.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_STRATEGY = StrategyId("swing-donchian-4h")
_T0 = datetime(2026, 10, 2, 16, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Return a ledger over an in-memory database, closed afterwards."""
    connection = sqlite3.connect(":memory:")
    yield SqliteLedger(connection)
    connection.close()


def _bar_processed(pair: Pair, interval: KlineInterval, close_time: datetime) -> BarProcessed:
    """Build a BarProcessed row for a stream at ``close_time``."""
    return BarProcessed(occurred_at=close_time, correlation_id=_CID, pair=pair, interval=interval)


def _signal(
    kind: SignalKind,
    when: datetime,
    *,
    pair: Pair = _BTC,
    strategy: StrategyId = _STRATEGY,
    exit_reason: str | None = None,
) -> SignalEmitted:
    """Build a long entry or exit event at ``when``."""
    return SignalEmitted.from_signal(
        StrategySignal(
            kind=kind,
            side=SignalSide.LONG,
            entry_price=Decimal("50000.5"),
            stop_price=Decimal("48000"),
            atr_context=Decimal("1200"),
            signal_time=when,
            exit_reason=exit_reason,
        ),
        strategy_id=strategy,
        pair=pair,
        correlation_id=_CID,
    )


class TestLastProcessedClose:
    """The cursor read is per stream and newest-wins."""

    def test_returns_none_when_the_stream_was_never_processed(self, ledger: SqliteLedger) -> None:
        """No row means no cursor."""
        cursors = (
            ledger.last_processed_close(_BTC, KlineInterval.H4),
            ledger.last_processed_close(_BTC, KlineInterval.D1),
        )

        assert cursors == (None, None)

    def test_returns_the_close_time_of_the_row(self, ledger: SqliteLedger) -> None:
        """A single row is returned as a UTC-aware datetime equal to what was written."""
        ledger.append_event(_bar_processed(_BTC, KlineInterval.H4, _T0))

        assert ledger.last_processed_close(_BTC, KlineInterval.H4) == _T0

    def test_newest_row_wins_regardless_of_insertion_order(self, ledger: SqliteLedger) -> None:
        """The newest occurred_at is returned even when written first."""
        ledger.append_event(_bar_processed(_BTC, KlineInterval.H4, _T0 + timedelta(hours=8)))
        ledger.append_event(_bar_processed(_BTC, KlineInterval.H4, _T0))

        assert ledger.last_processed_close(_BTC, KlineInterval.H4) == _T0 + timedelta(hours=8)

    def test_other_pair_is_not_read(self, ledger: SqliteLedger) -> None:
        """A row of another pair does not answer for this pair."""
        ledger.append_event(_bar_processed(_ETH, KlineInterval.H4, _T0))

        assert (
            ledger.last_processed_close(_ETH, KlineInterval.H4),
            ledger.last_processed_close(_BTC, KlineInterval.H4),
        ) == (_T0, None)

    def test_other_interval_is_not_read(self, ledger: SqliteLedger) -> None:
        """A row of another interval does not answer for this interval."""
        ledger.append_event(_bar_processed(_BTC, KlineInterval.D1, _T0))

        assert (
            ledger.last_processed_close(_BTC, KlineInterval.D1),
            ledger.last_processed_close(_BTC, KlineInterval.H4),
        ) == (_T0, None)

    def test_a_closed_connection_raises_ledger_error(self) -> None:
        """A SQLite failure surfaces as LedgerError."""
        connection = sqlite3.connect(":memory:")
        broken = SqliteLedger(connection)
        connection.close()

        with pytest.raises(LedgerError):
            broken.last_processed_close(_BTC, KlineInterval.H4)


class TestLatestSignalEvent:
    """The position-restore read is per strategy and pair, bounded in time."""

    def test_returns_none_without_a_row(self, ledger: SqliteLedger) -> None:
        """No signal means flat."""
        found = [ledger.latest_signal_event(_STRATEGY, _BTC, at_or_before=_T0)]

        assert found == [None]

    def test_returns_the_entry_rehydrated_field_for_field(self, ledger: SqliteLedger) -> None:
        """The stored entry comes back equal to the event that was written."""
        entry = _signal(SignalKind.ENTRY, _T0)
        ledger.append_event(entry)

        assert ledger.latest_signal_event(_STRATEGY, _BTC, at_or_before=_T0) == entry

    def test_exit_after_entry_is_the_latest(self, ledger: SqliteLedger) -> None:
        """An exit written after an entry is the one returned."""
        ledger.append_event(_signal(SignalKind.ENTRY, _T0))
        exit_ = _signal(SignalKind.EXIT, _T0 + timedelta(hours=8), exit_reason="chandelier")
        ledger.append_event(exit_)

        latest = ledger.latest_signal_event(_STRATEGY, _BTC, at_or_before=_T0 + timedelta(hours=8))

        assert latest is not None
        assert (latest.kind, latest.exit_reason) == (SignalKind.EXIT, "chandelier")

    def test_at_or_before_bound_is_inclusive_and_ignores_newer_rows(
        self, ledger: SqliteLedger
    ) -> None:
        """A signal after the bound is ignored; one exactly at the bound is returned."""
        entry = _signal(SignalKind.ENTRY, _T0)
        ledger.append_event(entry)
        ledger.append_event(
            _signal(SignalKind.EXIT, _T0 + timedelta(hours=4), exit_reason="chandelier")
        )

        assert ledger.latest_signal_event(_STRATEGY, _BTC, at_or_before=_T0) == entry

    def test_other_pair_is_not_read(self, ledger: SqliteLedger) -> None:
        """A signal of another pair does not restore this pair's position."""
        ledger.append_event(_signal(SignalKind.ENTRY, _T0, pair=_ETH))

        found = [ledger.latest_signal_event(_STRATEGY, _BTC, at_or_before=_T0)]

        assert found == [None]

    def test_other_strategy_is_not_read(self, ledger: SqliteLedger) -> None:
        """A signal of another strategy bucket is not read."""
        ledger.append_event(_signal(SignalKind.ENTRY, _T0, strategy=StrategyId("other")))

        found = [ledger.latest_signal_event(_STRATEGY, _BTC, at_or_before=_T0)]

        assert found == [None]

    def test_an_unreadable_payload_raises_ledger_error(self, ledger: SqliteLedger) -> None:
        """A stored row missing a field is reported, not silently skipped."""
        ledger.append_event(_signal(SignalKind.ENTRY, _T0))
        ledger._conn.execute(
            "UPDATE events SET payload_json = "
            "json_remove(payload_json, '$.stop_price') WHERE event_type = 'SignalEmitted'"
        )

        with pytest.raises(LedgerError, match="unreadable payload"):
            ledger.latest_signal_event(_STRATEGY, _BTC, at_or_before=_T0)


class TestHasSignalEvent:
    """Duplicate detection matches strategy, pair, kind and time."""

    def test_found_after_it_was_written(self, ledger: SqliteLedger) -> None:
        """The written event is found."""
        event = _signal(SignalKind.ENTRY, _T0)
        ledger.append_event(event)

        assert ledger.has_signal_event(event) is True

    def test_absent_on_an_empty_ledger(self, ledger: SqliteLedger) -> None:
        """Nothing written means nothing found."""
        assert ledger.has_signal_event(_signal(SignalKind.ENTRY, _T0)) is False

    def test_other_pair_is_not_a_match(self, ledger: SqliteLedger) -> None:
        """The same kind and time on another pair is a different signal."""
        ledger.append_event(_signal(SignalKind.ENTRY, _T0, pair=_ETH))

        assert ledger.has_signal_event(_signal(SignalKind.ENTRY, _T0)) is False

    def test_other_kind_is_not_a_match(self, ledger: SqliteLedger) -> None:
        """An exit at the same time does not match an entry."""
        ledger.append_event(_signal(SignalKind.EXIT, _T0, exit_reason="chandelier"))

        assert ledger.has_signal_event(_signal(SignalKind.ENTRY, _T0)) is False

    def test_other_time_is_not_a_match(self, ledger: SqliteLedger) -> None:
        """The same signal one bar later is a new signal."""
        ledger.append_event(_signal(SignalKind.ENTRY, _T0))

        assert ledger.has_signal_event(_signal(SignalKind.ENTRY, _T0 + timedelta(hours=4))) is False

    def test_a_correlation_id_difference_does_not_hide_a_duplicate(
        self, ledger: SqliteLedger
    ) -> None:
        """A re-evaluated bar gets a new correlation id but is still the same signal."""
        ledger.append_event(_signal(SignalKind.ENTRY, _T0))
        again = SignalEmitted.from_signal(
            StrategySignal(
                kind=SignalKind.ENTRY,
                side=SignalSide.LONG,
                entry_price=Decimal("50000.5"),
                stop_price=Decimal("48000"),
                atr_context=Decimal("1200"),
                signal_time=_T0,
            ),
            strategy_id=_STRATEGY,
            pair=_BTC,
            correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH2",
        )

        assert ledger.has_signal_event(again) is True
