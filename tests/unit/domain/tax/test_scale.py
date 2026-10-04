"""Tests for SavingsScale (change tax-estimates R-1).

Reference: ADR-016 decision 1, IRPF art. 66 and 76.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.tax.scale import BandPosition, SavingsBand, SavingsScale
from tests.unit.domain.tax.scales import SCALE_2026

_bases = st.decimals(min_value=-1000, max_value=1_000_000, places=2, allow_nan=False)


@pytest.mark.parametrize(
    ("base", "tax"),
    [
        ("6000", "1140.00"),
        ("50000", "10380.00"),
        ("200000", "44880.00"),
        ("300000", "71880.00"),
        ("300100", "71910.00"),
    ],
)
def test_tax_at_each_band_edge_matches_the_worked_figure(base: str, tax: str) -> None:
    """The cumulative tax at each band limit is the sum of full lower bands."""
    assert SCALE_2026.tax(Decimal(base)) == Decimal(tax)


def test_tax_one_euro_past_an_edge_is_taxed_at_the_next_rate() -> None:
    """The euro after 6,000 is taxed at 21%, not 19%."""
    assert SCALE_2026.tax(Decimal("6001")) - SCALE_2026.tax(Decimal("6000")) == Decimal("0.21")


@pytest.mark.parametrize("base", ["0", "-50"])
def test_tax_on_a_non_positive_base_is_zero(base: str) -> None:
    """No base, no tax; a negative base is never a negative tax."""
    assert SCALE_2026.tax(Decimal(base)) == Decimal(0)


@given(a=_bases, b=_bases)
def test_tax_is_monotonic_in_base(a: Decimal, b: Decimal) -> None:
    """A larger base never owes less tax."""
    low, high = sorted((a, b))
    assert SCALE_2026.tax(low) <= SCALE_2026.tax(high)


@given(base=_bases)
def test_tax_never_exceeds_the_top_rate_times_base(base: Decimal) -> None:
    """The average rate is bounded by the top marginal rate."""
    assert SCALE_2026.tax(base) <= max(base, Decimal(0)) * Decimal("0.30")


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("0", BandPosition(Decimal("0.19"), Decimal("0.21"), Decimal("6000"))),
        ("5000", BandPosition(Decimal("0.19"), Decimal("0.21"), Decimal("1000"))),
        ("6000", BandPosition(Decimal("0.21"), Decimal("0.23"), Decimal("44000"))),
        ("-10", BandPosition(Decimal("0.19"), Decimal("0.21"), Decimal("6000"))),
        ("300000", BandPosition(Decimal("0.30"), None, None)),
    ],
)
def test_band_at_names_the_band_of_the_next_euro(base: str, expected: BandPosition) -> None:
    """At a band limit the next euro is in the next band; the top band has no next."""
    assert SCALE_2026.band_at(Decimal(base)) == expected


@pytest.mark.parametrize(
    "bands",
    [
        (),
        (SavingsBand(up_to=Decimal("10"), rate=Decimal("0.1")),),
        (
            SavingsBand(up_to=None, rate=Decimal("0.1")),
            SavingsBand(up_to=None, rate=Decimal("0.2")),
        ),
        (
            SavingsBand(up_to=Decimal("10"), rate=Decimal("0.1")),
            SavingsBand(up_to=Decimal("10"), rate=Decimal("0.2")),
            SavingsBand(up_to=None, rate=Decimal("0.3")),
        ),
    ],
)
def test_scale_rejects_malformed_bands(bands: tuple[SavingsBand, ...]) -> None:
    """Empty, bounded-top, unbounded-middle and non-ascending scales fail closed."""
    with pytest.raises(ValueError, match=r"(?i)savings"):
        SavingsScale(bands=bands)


@pytest.mark.parametrize(("up_to", "rate"), [("0", "0.1"), ("10", "1.5"), ("10", "-0.1")])
def test_band_rejects_bad_limit_or_rate(up_to: str, rate: str) -> None:
    """A band limit is positive and a rate is a fraction in [0, 1]."""
    with pytest.raises(ValueError, match="SavingsBand"):
        SavingsBand(up_to=Decimal(up_to), rate=Decimal(rate))
