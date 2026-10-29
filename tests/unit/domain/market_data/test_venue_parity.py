"""Unit tests for comparing primary and backup venue closes (#31).

Invariants under test:

- ``divergence_bps`` is the absolute relative difference in basis points
  and refuses a non-positive reference price.
- Identical closes on both venues are OK with zero divergence.
- A close beyond the tolerance makes the stream DIVERGED; one at the
  tolerance does not.
- A bar on one venue only makes the stream INCOMPLETE, and that wins over
  a divergent close.
- ``worst`` is the bar with the largest divergence.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.venue_parity import (
    ParityVerdict,
    compare_closes,
    divergence_bps,
)
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2026, 10, 3, tzinfo=UTC)
_H4 = timedelta(hours=4)
_TOLERANCE = Decimal(50)


def _bars(closes: list[str]) -> dict[datetime, Decimal]:
    return {_T0 + _H4 * i: Decimal(c) for i, c in enumerate(closes)}


def _compare(
    primary: dict[datetime, Decimal], backup: dict[datetime, Decimal]
) -> tuple[ParityVerdict, int]:
    result = compare_closes(
        pair=_BTC,
        interval=KlineInterval.H4,
        primary=primary,
        backup=backup,
        tolerance_bps=_TOLERANCE,
    )
    return result.verdict, result.compared


def test_divergence_is_relative_to_the_primary_close() -> None:
    """100 against 101 is 1 percent of the primary, so 100 bps."""
    assert divergence_bps(Decimal(100), Decimal(101)) == Decimal(100)


def test_divergence_ignores_the_sign() -> None:
    """A backup close below the primary diverges by the same amount as one above."""
    assert divergence_bps(Decimal(100), Decimal(99)) == Decimal(100)


@pytest.mark.parametrize("primary", [Decimal(0), Decimal(-1)])
def test_divergence_refuses_a_non_positive_reference(primary: Decimal) -> None:
    """A zero or negative primary close is bad data, not a zero divergence."""
    with pytest.raises(ValueError, match="must be positive"):
        divergence_bps(primary, Decimal(1))


def test_identical_closes_are_ok() -> None:
    """Six equal bars on both venues pass with all six compared."""
    day = _bars(["84575.5", "84673.0", "84700.1", "84690.0", "84710.2", "84753.5"])
    assert _compare(day, dict(day)) == (ParityVerdict.OK, 6)


def test_close_beyond_tolerance_diverges() -> None:
    """One bar 1 percent apart is beyond a 50 bps tolerance."""
    assert _compare(_bars(["100", "100"]), _bars(["100", "101"])) == (ParityVerdict.DIVERGED, 2)


def test_close_at_tolerance_is_ok() -> None:
    """Exactly 50 bps apart is still within a 50 bps tolerance."""
    assert _compare(_bars(["100"]), _bars(["100.5"])) == (ParityVerdict.OK, 1)


def test_bar_missing_on_backup_is_incomplete() -> None:
    """The backup lacking the newest bar would leave the strategy a gap."""
    result = compare_closes(
        pair=_BTC,
        interval=KlineInterval.H4,
        primary=_bars(["100", "100"]),
        backup=_bars(["100"]),
        tolerance_bps=_TOLERANCE,
    )
    assert (result.verdict, result.missing_on_backup) == (ParityVerdict.INCOMPLETE, (_T0 + _H4,))


def test_bar_missing_on_primary_is_incomplete() -> None:
    """A bar only the backup has means the two venues disagree on the timeline."""
    result = compare_closes(
        pair=_BTC,
        interval=KlineInterval.H4,
        primary=_bars(["100"]),
        backup=_bars(["100", "100"]),
        tolerance_bps=_TOLERANCE,
    )
    assert (result.verdict, result.missing_on_primary) == (ParityVerdict.INCOMPLETE, (_T0 + _H4,))


def test_missing_bar_wins_over_divergence() -> None:
    """A gap is reported even when a shared bar also diverges."""
    assert _compare(_bars(["100", "100"]), _bars(["200"])) == (ParityVerdict.INCOMPLETE, 1)


def test_nothing_to_compare_is_incomplete() -> None:
    """Two empty venues prove nothing, so the stream is not OK."""
    result = compare_closes(
        pair=_BTC, interval=KlineInterval.H4, primary={}, backup={}, tolerance_bps=_TOLERANCE
    )
    assert (result.verdict, result.worst) == (ParityVerdict.INCOMPLETE, None)


def test_worst_is_the_largest_divergence() -> None:
    """Of three divergent bars, the 3 percent one is reported."""
    result = compare_closes(
        pair=_BTC,
        interval=KlineInterval.H4,
        primary=_bars(["100", "100", "100"]),
        backup=_bars(["101", "103", "98"]),
        tolerance_bps=_TOLERANCE,
    )
    assert result.worst is not None
    assert (result.worst.open_time, result.worst.bps) == (_T0 + _H4, Decimal(300))


_closes = st.lists(
    st.decimals(min_value=Decimal("0.01"), max_value=Decimal(10**6), places=2),
    min_size=1,
    max_size=12,
)


@given(closes=_closes, shift_bps=st.integers(min_value=0, max_value=1_000))
def test_verdict_follows_the_tolerance(closes: list[Decimal], shift_bps: int) -> None:
    """Shifting every backup close by k bps diverges exactly when k exceeds the tolerance."""
    primary = {_T0 + _H4 * i: c for i, c in enumerate(closes)}
    factor = 1 + Decimal(shift_bps) / Decimal(10_000)
    backup = {t: c * factor for t, c in primary.items()}
    expected = ParityVerdict.DIVERGED if shift_bps > _TOLERANCE else ParityVerdict.OK
    assert _compare(primary, backup) == (expected, len(closes))
