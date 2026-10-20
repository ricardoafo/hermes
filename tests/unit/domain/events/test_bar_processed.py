"""Tests for the BarProcessed domain event.

Invariants protected here: the event is a versioned, hashed record whose time
is the bar's close time (never a wall clock), so the ledger cursor replays
deterministically; a naive timestamp is refused.

Reference: 309-serve-bar-loop change.md D-6.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_PAIR = Pair(base=Currency.ETH, quote=Currency.USDT)
_OPEN = datetime(2026, 10, 2, 12, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"


def _bar() -> Bar:
    """Return a 4h bar opening at ``_OPEN``."""
    return Bar(
        pair=_PAIR,
        interval=KlineInterval.H4,
        open_time=_OPEN,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10"),
        volume=Decimal("1"),
        close_time=_OPEN + timedelta(hours=4) - timedelta(milliseconds=1),
    )


def test_from_bar_takes_occurred_at_from_the_bar_close_time() -> None:
    """occurred_at is the bar's close_time, so the cursor needs no clock."""
    bar = _bar()

    event = BarProcessed.from_bar(bar, correlation_id=_CID)

    assert (event.occurred_at, event.pair, event.interval) == (
        bar.close_time,
        _PAIR,
        KlineInterval.H4,
    )


def test_event_schema_version_is_one() -> None:
    """The event declares schema version 1 for the upcaster chain."""
    assert BarProcessed.__event_schema_version__ == 1


def test_payload_hash_is_set_and_stable() -> None:
    """Two events built from the same bar hash identically and the hash is filled."""
    first = BarProcessed.from_bar(_bar(), correlation_id=_CID)
    second = BarProcessed.from_bar(_bar(), correlation_id=_CID)

    assert (len(first.payload_sha256), first.payload_sha256) == (64, second.payload_sha256)


def test_naive_occurred_at_is_rejected() -> None:
    """A naive occurred_at is refused."""
    with pytest.raises(ValueError, match="UTC-aware"):
        BarProcessed(
            occurred_at=datetime(2026, 10, 2, 16),
            correlation_id=_CID,
            pair=_PAIR,
            interval=KlineInterval.H4,
        )
