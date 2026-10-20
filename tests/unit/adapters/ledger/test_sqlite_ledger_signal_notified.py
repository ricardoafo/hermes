"""Tests for the ledger reads behind the at-least-once signal push (#854).

Invariants protected here:

- ``latest_signal_notified`` returns the newest push record of one signal,
  matched by its correlation id, rehydrated field for field;
- ``signals_owed_a_push`` lists a signal only while its newest push record
  names a failed channel, with those channels, inside the time bounds, for one
  strategy and pair; a signal never pushed is not listed.

All run against SQLite ``:memory:``.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.signal_notified import SignalNotified
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_STRATEGY = StrategyId("swing-donchian-4h")
_T0 = datetime(2026, 10, 2, 16, tzinfo=UTC)
_WIDE = {"since": _T0 - timedelta(days=1), "until": _T0 + timedelta(days=1)}


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Return a ledger over an in-memory database, closed afterwards."""
    connection = sqlite3.connect(":memory:")
    yield SqliteLedger(connection)
    connection.close()


def _signal(cid: str, when: datetime = _T0, *, pair: Pair = _BTC) -> SignalEmitted:
    """Build a long entry at ``when`` with correlation id ``cid``."""
    return SignalEmitted.from_signal(
        StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=Decimal("50000.5"),
            stop_price=Decimal(48000),
            atr_context=Decimal(1200),
            signal_time=when,
        ),
        strategy_id=_STRATEGY,
        pair=pair,
        correlation_id=cid,
    )


def _pushed(
    ledger: SqliteLedger,
    signal: SignalEmitted,
    *,
    delivered: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
) -> SignalNotified:
    """Store one push record of ``signal`` and return it."""
    record = SignalNotified.after(signal, delivered=delivered, failed=failed)
    ledger.append_event(record)
    return record


class TestLatestSignalNotified:
    """The push record read is per signal and newest-wins."""

    def test_none_for_a_signal_never_pushed(self, ledger: SqliteLedger) -> None:
        """A stored signal with no push record has none."""
        signal = _signal("A")
        ledger.append_event(signal)

        assert (ledger.has_signal_event(signal), ledger.latest_signal_notified(signal)) == (
            True,
            None,
        )

    def test_the_newest_record_is_returned_field_for_field(self, ledger: SqliteLedger) -> None:
        """After a failed push and a retry, the retry's record comes back intact."""
        signal = _signal("A")
        ledger.append_event(signal)
        _pushed(ledger, signal, delivered=("slack",), failed=("telegram",))
        retry = _pushed(ledger, signal, delivered=("telegram",))

        assert ledger.latest_signal_notified(signal) == retry

    def test_another_signals_record_does_not_answer(self, ledger: SqliteLedger) -> None:
        """A record keyed to another correlation id is not this signal's."""
        ours, theirs = _signal("A"), _signal("B", _T0 + timedelta(hours=4))
        ledger.append_event(ours)
        ledger.append_event(theirs)
        _pushed(ledger, theirs, delivered=("telegram",))

        assert (ledger.latest_signal_notified(ours), ledger.latest_signal_notified(theirs)) == (
            None,
            SignalNotified.after(theirs, delivered=("telegram",), failed=()),
        )


class TestSignalsOwedAPush:
    """Only signals whose newest push failed somewhere are owed one."""

    def test_a_failed_channel_is_owed_with_its_name(self, ledger: SqliteLedger) -> None:
        """Telegram raised, Slack took it: the signal is owed a Telegram push only."""
        signal = _signal("A")
        ledger.append_event(signal)
        _pushed(ledger, signal, delivered=("slack",), failed=("telegram",))

        assert ledger.signals_owed_a_push(_STRATEGY, _BTC, **_WIDE) == ((signal, ("telegram",)),)

    def test_a_successful_retry_settles_the_signal(self, ledger: SqliteLedger) -> None:
        """Once the newest record names no failure, nothing is owed."""
        signal = _signal("A")
        ledger.append_event(signal)
        _pushed(ledger, signal, failed=("telegram",))
        _pushed(ledger, signal, delivered=("telegram",))

        assert ledger.signals_owed_a_push(_STRATEGY, _BTC, **_WIDE) == ()

    def test_a_signal_never_pushed_is_not_owed(self, ledger: SqliteLedger) -> None:
        """No record means the emitting bar has not pushed it yet; the sweep leaves it alone."""
        ledger.append_event(_signal("A"))

        assert ledger.signals_owed_a_push(_STRATEGY, _BTC, **_WIDE) == ()

    def test_a_signal_outside_the_bounds_is_not_owed(self, ledger: SqliteLedger) -> None:
        """A failed push older than ``since`` is stale and no longer owed."""
        signal = _signal("A")
        ledger.append_event(signal)
        _pushed(ledger, signal, failed=("telegram",))

        owed = ledger.signals_owed_a_push(
            _STRATEGY, _BTC, since=_T0 + timedelta(seconds=1), until=_T0 + timedelta(hours=8)
        )

        assert owed == ()

    def test_another_pairs_signal_is_not_owed(self, ledger: SqliteLedger) -> None:
        """A failed ETH push is not owed on the BTC sweep."""
        signal = _signal("A", pair=_ETH)
        ledger.append_event(signal)
        _pushed(ledger, signal, failed=("telegram",))

        assert ledger.signals_owed_a_push(_STRATEGY, _BTC, **_WIDE) == ()

    def test_owed_signals_come_oldest_first(self, ledger: SqliteLedger) -> None:
        """Two owed signals are listed in signal-time order, whatever the write order."""
        later, earlier = _signal("B", _T0 + timedelta(hours=4)), _signal("A")
        for signal in (later, earlier):
            ledger.append_event(signal)
            _pushed(ledger, signal, failed=("telegram",))

        owed = ledger.signals_owed_a_push(_STRATEGY, _BTC, **_WIDE)

        assert [s.correlation_id for s, _ in owed] == ["A", "B"]
