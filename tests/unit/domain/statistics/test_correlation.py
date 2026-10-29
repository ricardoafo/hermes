"""Unit tests for Pearson's coefficient, the one copy every gate uses (#835, #1179).

Invariants under test:

- Pearson's coefficient matches a hand-computed value, is ``None`` when it is
  undefined (fewer than two points, a flat series, a non-finite value) and
  refuses unequal lengths.
- For any two series it lies in [-1, 1], is symmetric, and is 1.0 for a
  series with itself (property).
- A constant series is ``None`` at any length and level (property), so no gate
  reads a one-ulp noise value as a correlation.
"""

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from hermes.domain.statistics.correlation import pearson

_returns = st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False)
_series = st.lists(_returns, min_size=2, max_size=40)


def test_pearson_of_a_known_pair_of_series() -> None:
    """Series 1..5 against 2, 4, 5, 4, 5 gives r = sqrt(0.6) (hand-computed)."""
    value = pearson([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 4.0, 5.0, 4.0, 5.0])
    assert value == pytest.approx(math.sqrt(0.6), abs=1e-12)


def test_pearson_of_opposite_series_is_minus_one() -> None:
    """A series against its negation is perfectly anti-correlated."""
    assert pearson([0.01, -0.02, 0.03], [-0.01, 0.02, -0.03]) == pytest.approx(-1.0)


@pytest.mark.parametrize(
    ("xs", "ys"),
    [([], []), ([0.1], [0.2]), ([0.1, 0.1, 0.1], [0.1, 0.2, 0.3])],
)
def test_pearson_is_undefined_without_two_points_or_variance(
    xs: list[float], ys: list[float]
) -> None:
    """No value is invented when the coefficient does not exist."""
    assert [pearson(xs, ys)] == [None]


def test_pearson_survives_variances_whose_product_underflows() -> None:
    """Two tiny variances (~1e-242) multiply to 0.0; the coefficient is still 1.0 (hypothesis)."""
    series = [0.0, 3.462674987231344e-121]
    assert pearson(series, series) == pytest.approx(1.0)


def test_pearson_refuses_series_of_different_length() -> None:
    """Misaligned series are a caller bug, not a correlation."""
    with pytest.raises(ValueError, match="length"):
        pearson([0.1, 0.2], [0.1, 0.2, 0.3])


def test_pearson_refuses_a_first_series_longer_than_the_second() -> None:
    """The length check holds both ways round, not only when the first is shorter (#1219)."""
    with pytest.raises(ValueError, match="length"):
        pearson([0.1, 0.2, 0.3], [0.1, 0.2])


@pytest.mark.parametrize(
    ("ys", "expected"),
    [([3.0, 5.0], 1.0), ([5.0, 3.0], -1.0)],
    ids=["rising", "falling"],
)
def test_pearson_of_exactly_two_points_is_plus_or_minus_one(
    ys: list[float], expected: float
) -> None:
    """Two points with variance always lie on a line, so r is defined: +1 or -1 (#1219)."""
    assert pearson([1.0, 2.0], ys) == pytest.approx(expected, abs=1e-12)


@pytest.mark.property
@given(xs=_series, data=st.data())
def test_pearson_is_bounded_and_symmetric(xs: list[float], data: st.DataObject) -> None:
    """Any defined coefficient is in [-1, 1] and the same with the series swapped."""
    ys = data.draw(st.lists(_returns, min_size=len(xs), max_size=len(xs)))
    value = pearson(xs, ys)
    swapped = pearson(ys, xs)
    if value is None:
        assert swapped is None
        return
    assert swapped == pytest.approx(value, abs=1e-12)
    assert -1.0 <= value <= 1.0


@pytest.mark.property
@given(xs=_series)
def test_pearson_of_a_series_with_itself_is_one(xs: list[float]) -> None:
    """Identical series correlate at 1.0 whenever the series varies."""
    mean = math.fsum(xs) / len(xs)
    assume(math.fsum((x - mean) ** 2 for x in xs) > 1e-12)
    assert pearson(xs, xs) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize(
    "bad",
    [math.nan, math.inf, -math.inf],
    ids=["nan", "inf", "minus-inf"],
)
def test_pearson_is_undefined_with_a_non_finite_value(bad: float) -> None:
    """A NaN or infinity anywhere gives None, never the clamp's 1.0 or an fsum error."""
    assert [pearson([0.1, bad, 0.3], [0.2, 0.1, 0.4])] == [None]


def test_pearson_is_undefined_when_the_coefficient_overflows() -> None:
    """Values near the float limit overflow the variance; the result is None, not 0.0."""
    assert [pearson([1e308, -1e308, 1e308], [1.0, 2.0, 4.0])] == [None]


@pytest.mark.property
@given(
    level=st.floats(allow_nan=False, allow_infinity=False),
    length=st.integers(min_value=0, max_value=40),
    data=st.data(),
)
def test_a_constant_series_is_undefined(level: float, length: int, data: st.DataObject) -> None:
    """A series with one repeated value has no correlation with anything (#1179 Guard)."""
    other = data.draw(st.lists(_returns, min_size=length, max_size=length))
    assert [pearson([level] * length, other)] == [None]
    assert [pearson(other, [level] * length)] == [None]
