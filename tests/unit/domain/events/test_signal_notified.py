"""Tests for ``SignalNotified`` — the record of one push of a signal (#854).

Invariants protected here:

- the record is keyed to its signal: same ``correlation_id``, same time;
- it is settled exactly when no channel failed, even with no channel wired;
- a channel cannot be both delivered and failed;
- a naive time is refused.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.signal_notified import SignalNotified
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_T0 = datetime(2026, 10, 2, 16, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"


def _signal() -> SignalEmitted:
    """Return a long entry event at ``_T0``."""
    return SignalEmitted.from_signal(
        StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=Decimal(50000),
            stop_price=Decimal(48000),
            atr_context=Decimal(1200),
            signal_time=_T0,
        ),
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        correlation_id=_CID,
    )


def test_after_keys_the_record_to_the_signal() -> None:
    """The record carries the signal's correlation id and time, not a wall clock."""
    record = SignalNotified.after(_signal(), delivered=("telegram",), failed=())

    assert (record.correlation_id, record.occurred_at) == (_CID, _T0)


@pytest.mark.parametrize(
    ("delivered", "failed", "settled"),
    [
        (("telegram", "slack"), (), True),
        ((), (), True),
        (("slack",), ("telegram",), False),
        ((), ("telegram",), False),
    ],
)
def test_settled_exactly_when_no_channel_failed(
    delivered: tuple[str, ...], failed: tuple[str, ...], settled: bool
) -> None:
    """A failed channel leaves the signal owed a push; none failed settles it."""
    record = SignalNotified.after(_signal(), delivered=delivered, failed=failed)

    assert record.settled is settled


def test_a_channel_in_both_sets_is_refused() -> None:
    """Delivered and failed are disjoint: one push cannot both reach and miss a channel."""
    with pytest.raises(ValueError, match="delivered and failed"):
        SignalNotified.after(_signal(), delivered=("telegram",), failed=("telegram",))


def test_a_naive_time_is_refused() -> None:
    """The record must carry a UTC-aware time, as the signal does."""
    with pytest.raises(ValueError, match="UTC-aware"):
        SignalNotified(
            occurred_at=datetime(2026, 10, 2, 16),
            correlation_id=_CID,
            delivered=(),
            failed=(),
        )
