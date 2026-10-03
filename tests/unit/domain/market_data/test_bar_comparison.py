"""Unit tests for comparing live bars with archived reference bars (#359).

Invariants under test:

- Identical bars produce no mismatch; any OHLCV difference beyond the
  tolerance produces exactly one VALUE mismatch naming the field.
- A bar on one side only is reported as missing or extra.
- ``close_time`` is compared to the millisecond (archive microseconds vs
  live milliseconds).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_comparison import MismatchKind, compare_bars
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2025, 8, 1, tzinfo=UTC)
_H4 = timedelta(hours=4)


def _bar(i: int) -> Bar:
    open_time = _T0 + _H4 * i
    return Bar(
        pair=_BTC,
        interval=KlineInterval.H4,
        open_time=open_time,
        close_time=open_time + _H4 - timedelta(milliseconds=1),
        open=Decimal("115764.07"),
        high=Decimal("116052.00"),
        low=Decimal("114313.13"),
        close=Decimal("115648.03"),
        volume=Decimal("5440.32571"),
    )


def test_identical_days_match() -> None:
    """Six equal bars on both sides is a clean day."""
    day = [_bar(i) for i in range(6)]
    assert compare_bars(day, list(reversed(day))) == ()


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_one_changed_field_is_one_value_mismatch(field: str) -> None:
    """A one-unit change in the last decimal of any OHLCV field is caught and named."""
    reference = _bar(2)
    tiny = Decimal("0.00000001")
    live = replace(
        reference,
        **{field: getattr(reference, field) + (tiny if field != "low" else -tiny)},
    )
    (mismatch,) = compare_bars([live], [reference])
    assert mismatch.kind is MismatchKind.VALUE
    assert mismatch.open_time == reference.open_time
    assert mismatch.detail.startswith(f"{field} live=")


def test_difference_within_tolerance_matches() -> None:
    """A stated tolerance absorbs differences no larger than itself."""
    reference = _bar(0)
    live = replace(reference, close=reference.close + Decimal("0.01"))
    assert compare_bars([live], [reference], tolerance=Decimal("0.01")) == ()
    assert len(compare_bars([live], [reference], tolerance=Decimal("0.009"))) == 1


def test_missing_and_extra_bars_are_named() -> None:
    """A bar only in the archive is missing live; a bar only live is extra."""
    got = compare_bars([_bar(0), _bar(2)], [_bar(0), _bar(1)])
    assert [(m.kind, m.open_time) for m in got] == [
        (MismatchKind.MISSING_LIVE, _bar(1).open_time),
        (MismatchKind.EXTRA_LIVE, _bar(2).open_time),
    ]


def test_archive_microsecond_close_time_equals_live_millisecond() -> None:
    """The archive's ...59.999999 close is the live feed's ...59.999 close."""
    live = _bar(0)
    archive = replace(live, close_time=live.close_time + timedelta(microseconds=999))
    assert compare_bars([live], [archive]) == ()


def test_close_time_off_by_a_millisecond_is_a_mismatch() -> None:
    """Close times that differ at millisecond precision are reported."""
    live = _bar(0)
    archive = replace(live, close_time=live.close_time - timedelta(milliseconds=1))
    (mismatch,) = compare_bars([live], [archive])
    assert "close_time" in mismatch.detail


def test_negative_tolerance_is_rejected() -> None:
    """A negative tolerance has no meaning."""
    with pytest.raises(ValueError, match="tolerance"):
        compare_bars([], [], tolerance=Decimal(-1))


@given(st.sets(st.integers(0, 11), max_size=12), st.sets(st.integers(0, 11), max_size=12))
def test_presence_mismatches_are_the_symmetric_difference(live: set[int], ref: set[int]) -> None:
    """Every bar on exactly one side yields one presence mismatch, and no others appear."""
    got = compare_bars([_bar(i) for i in live], [_bar(i) for i in ref])
    assert {m.open_time for m in got} == {_bar(i).open_time for i in live ^ ref}
