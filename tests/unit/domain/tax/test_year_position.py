"""Tests for tax years and yearly positions (change tax-estimates D-2).

Reference: ADR-016 decisions 2 and 6.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.tax.year_position import Disposal, YearPosition, tax_year_of

_D = Decimal


def _sale(ref: str, at: datetime, gain: str) -> Disposal:
    return Disposal(asset="BTC", ref=ref, at=at, gain=_D(gain), consumed_refs=frozenset())


def test_late_utc_new_year_eve_counts_in_the_next_madrid_year() -> None:
    """23:30 UTC on 31 December is 00:30 on 1 January in Madrid."""
    assert tax_year_of(datetime(2026, 12, 31, 23, 30, tzinfo=UTC)) == 2027


def test_early_utc_new_year_eve_stays_in_the_year() -> None:
    """22:59 UTC on 31 December is still 31 December in Madrid."""
    assert tax_year_of(datetime(2026, 12, 31, 22, 59, tzinfo=UTC)) == 2026


def test_tax_year_rejects_naive_datetimes() -> None:
    """A naive instant has no Madrid date."""
    with pytest.raises(ValueError, match="timezone-aware"):
        tax_year_of(datetime(2026, 1, 1))


def test_disposal_rejects_naive_datetimes() -> None:
    """A sale time must be timezone-aware."""
    with pytest.raises(ValueError, match="timezone-aware"):
        _sale("1", datetime(2026, 1, 1), "1")


def test_year_position_splits_gains_and_losses_of_the_year_only() -> None:
    """Sales of other Madrid years are left out; losses are a positive magnitude."""
    sales = [
        _sale("1", datetime(2026, 3, 1, tzinfo=UTC), "500"),
        _sale("2", datetime(2026, 6, 1, tzinfo=UTC), "-200"),
        _sale("3", datetime(2026, 12, 31, 23, 30, tzinfo=UTC), "1000"),
        _sale("4", datetime(2025, 6, 1, tzinfo=UTC), "-50"),
    ]
    position = YearPosition.of(2026, sales)
    assert (position.gains, position.losses, position.net) == (_D("500"), _D("200"), _D("300"))


def test_year_position_rejects_negative_magnitudes() -> None:
    """Gains and losses are both stored as non-negative totals."""
    with pytest.raises(ValueError, match="non-negative"):
        YearPosition(year=2026, gains=_D("1"), losses=_D("-1"))
