"""Unit tests for the fold metrics of pre-registration §8.3 (issue #1181).

Invariants:

- A fold side reports the days it holds as inclusive date ranges, its day
  count, the per-day Sharpe of its returns and their compounded return.
- The purged CPCV train side never holds the 5 days before a test group or
  the 3 days after it, and holds the day just past each gap.
- The summary counts the folds whose test side made money and takes the
  median of the defined test Sharpes.

Every expected value is worked out by hand in the test or its comment.
"""

import math
import statistics
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.statistics.fold_metrics import (
    Fold,
    FoldSide,
    evaluate_folds,
    summarise_folds,
)
from hermes.domain.statistics.performance import daily_returns
from hermes.domain.statistics.splits import Split, purged_cpcv_splits

_DAY0 = date(2021, 1, 1)
"""The day ``returns[0]`` is earned in every example below."""


def _held_out(group_start: int) -> Split:
    """Return the 50-day, 5-group, one-held-out CPCV split testing from ``group_start``."""
    return next(s for s in purged_cpcv_splits(50, test_groups=1) if s.test[0] == group_start)


def _side(days: int, sharpe: float | None, total_return: float | None) -> FoldSide:
    return FoldSide(ranges=(), days=days, sharpe=sharpe, total_return=total_return)


class TestFoldDates:
    """50 days from 1 Jan 2021 in 5 groups of 10; group 2 is days 20-29, 21-30 Jan."""

    def test_the_test_side_is_the_held_out_group(self) -> None:
        """§8.3: the held-out group is 21 Jan to 30 Jan, ten days."""
        (fold,) = evaluate_folds(_DAY0, [0.001] * 50, [_held_out(20)])
        assert fold.test.ranges == ((date(2021, 1, 21), date(2021, 1, 30)),)
        assert fold.test.days == 10

    def test_purge_drops_the_five_days_before_the_test_and_keeps_the_sixth(self) -> None:
        """§8.3 5-day purge: 16-20 Jan are dropped, 15 Jan (six days before) trains."""
        (fold,) = evaluate_folds(_DAY0, [0.001] * 50, [_held_out(20)])
        assert fold.train.ranges[0] == (date(2021, 1, 1), date(2021, 1, 15))

    def test_embargo_drops_the_three_days_after_the_test_and_keeps_the_fourth(self) -> None:
        """§8.3 3-day embargo: 31 Jan to 2 Feb are dropped, 3 Feb (four days after) trains."""
        (fold,) = evaluate_folds(_DAY0, [0.001] * 50, [_held_out(20)])
        assert fold.train.ranges[1] == (date(2021, 2, 3), date(2021, 2, 19))

    def test_train_days_are_the_series_less_test_purge_and_embargo(self) -> None:
        """50 days less 10 tested, 5 purged and 3 embargoed leave 32 training days."""
        (fold,) = evaluate_folds(_DAY0, [0.001] * 50, [_held_out(20)])
        assert fold.train.days == 32

    def test_a_first_group_has_no_purge_before_it(self) -> None:
        """Group 0 (1-10 Jan) has nothing before it; training starts after the embargo, 14 Jan."""
        (fold,) = evaluate_folds(_DAY0, [0.001] * 50, [_held_out(0)])
        assert fold.train.ranges == ((date(2021, 1, 14), date(2021, 2, 19)),)

    def test_a_series_shorter_than_a_split_refuses(self) -> None:
        """A split that names a day the series does not have is a caller error."""
        with pytest.raises(ValueError, match="split reaches day 49 of a 40-day series"):
            evaluate_folds(_DAY0, [0.001] * 40, [_held_out(20)])


class TestFoldMetrics:
    """Six returns, train on days 0-2, test on days 4-5; day 3 is in neither."""

    _RETURNS = (0.01, -0.01, 0.02, 0.0, 0.03, -0.02)
    _SPLIT = Split(train=(0, 1, 2), test=(4, 5))

    def test_test_sharpe_is_the_mean_over_the_sample_deviation(self) -> None:
        """[0.03, -0.02]: mean 0.005, sample sd sqrt(2 * 0.025^2) = 0.025 * sqrt(2)."""
        (fold,) = evaluate_folds(_DAY0, self._RETURNS, [self._SPLIT])
        assert fold.test.sharpe == pytest.approx(0.005 / (0.025 * math.sqrt(2)))

    def test_test_return_compounds_the_days(self) -> None:
        """1.03 * 0.98 - 1 = 0.0094."""
        (fold,) = evaluate_folds(_DAY0, self._RETURNS, [self._SPLIT])
        assert fold.test.total_return == pytest.approx(0.0094)

    def test_train_sharpe_uses_only_the_train_days(self) -> None:
        """[0.01, -0.01, 0.02]: mean 0.02/3, sample variance 4.6667e-4 / 2 = 2.3333e-4."""
        (fold,) = evaluate_folds(_DAY0, self._RETURNS, [self._SPLIT])
        mean = 0.02 / 3
        variance = ((0.01 - mean) ** 2 + (-0.01 - mean) ** 2 + (0.02 - mean) ** 2) / 2
        assert fold.train.sharpe == pytest.approx(mean / math.sqrt(variance))

    def test_train_return_compounds_only_the_train_days(self) -> None:
        """1.01 * 0.99 * 1.02 - 1 = 0.019898; day 3 and the test days are left out."""
        (fold,) = evaluate_folds(_DAY0, self._RETURNS, [self._SPLIT])
        assert fold.train.total_return == pytest.approx(0.019898)

    def test_ranges_skip_the_day_in_neither_side(self) -> None:
        """Train is 1-3 Jan, test 5-6 Jan; 4 Jan belongs to neither."""
        (fold,) = evaluate_folds(_DAY0, self._RETURNS, [self._SPLIT])
        assert (fold.train.ranges, fold.test.ranges) == (
            ((date(2021, 1, 1), date(2021, 1, 3)),),
            ((date(2021, 1, 5), date(2021, 1, 6)),),
        )

    def test_an_empty_side_has_no_sharpe_and_no_return(self) -> None:
        """No day: nothing to measure, so both metrics are not computed (None), not 0."""
        (fold,) = evaluate_folds(_DAY0, self._RETURNS, [Split(train=(), test=(4, 5))])
        assert fold.train == FoldSide(ranges=(), days=0, sharpe=None, total_return=None)

    def test_a_single_day_side_has_a_return_and_no_sharpe(self) -> None:
        """One return has no sample deviation: Sharpe undefined, return 0.03."""
        (fold,) = evaluate_folds(_DAY0, self._RETURNS, [Split(train=(0,), test=(4,))])
        assert fold.test.sharpe is None
        assert fold.test.total_return == pytest.approx(0.03)


class TestSummary:
    """Counts of money-making test sides and the median test Sharpe."""

    @staticmethod
    def _fold(test_sharpe: float | None, test_return: float | None) -> Fold:
        return Fold(train=_side(0, None, None), test=_side(2, test_sharpe, test_return))

    def test_a_flat_test_side_is_not_positive(self) -> None:
        """Returns 0.0094, 0.0 and -0.01: only the first made money."""
        summary = summarise_folds(
            [self._fold(0.1, 0.0094), self._fold(0.2, 0.0), self._fold(0.3, -0.01)]
        )
        assert summary.positive_test_folds == 1

    def test_the_smallest_gain_is_positive(self) -> None:
        """A test return of 1e-12, just above 0, counts."""
        summary = summarise_folds([self._fold(0.1, 1e-12)])
        assert summary.positive_test_folds == 1

    def test_the_median_skips_undefined_sharpes(self) -> None:
        """Sharpes 0.1, undefined, 0.3, 0.2: the median of the three defined is 0.2."""
        summary = summarise_folds(
            [
                self._fold(0.1, 0.01),
                self._fold(None, 0.01),
                self._fold(0.3, 0.01),
                self._fold(0.2, 0.01),
            ]
        )
        assert summary.median_test_sharpe == pytest.approx(0.2)

    def test_the_summary_counts_every_fold(self) -> None:
        """Four folds, one without a test return: the count is still four."""
        summary = summarise_folds(
            [
                self._fold(0.1, 0.01),
                self._fold(None, None),
                self._fold(0.3, 0.01),
                self._fold(0.2, -0.01),
            ]
        )
        assert (summary.folds, summary.positive_test_folds) == (4, 2)

    def test_no_fold_gives_no_median(self) -> None:
        """A series too short for any fold: zero folds, nothing positive, no median."""
        summary = summarise_folds([])
        assert (summary.folds, summary.positive_test_folds, summary.median_test_sharpe) == (
            0,
            0,
            None,
        )


_EQUITY = st.lists(
    st.floats(min_value=100.0, max_value=10_000.0, allow_nan=False), min_size=3, max_size=60
)


@given(equity=_EQUITY, data=st.data())
def test_a_contiguous_test_return_is_the_equity_ratio(
    equity: list[float], data: st.DataObject
) -> None:
    """Compounding the daily returns of days a..b-1 gives equity[b] / equity[a] - 1."""
    returns = daily_returns(equity)
    a = data.draw(st.integers(min_value=0, max_value=len(returns) - 1))
    b = data.draw(st.integers(min_value=a + 1, max_value=len(returns)))
    (fold,) = evaluate_folds(_DAY0, returns, [Split(train=(), test=tuple(range(a, b)))])
    assert fold.test.total_return == pytest.approx(equity[b] / equity[a] - 1, rel=1e-9, abs=1e-12)


@given(equity=_EQUITY, data=st.data())
def test_a_contiguous_side_is_one_range_of_its_length(
    equity: list[float], data: st.DataObject
) -> None:
    """Days a..b-1 are the one range from day0 + a to day0 + b - 1, b - a days long."""
    returns = daily_returns(equity)
    a = data.draw(st.integers(min_value=0, max_value=len(returns) - 1))
    b = data.draw(st.integers(min_value=a + 1, max_value=len(returns)))
    (fold,) = evaluate_folds(_DAY0, returns, [Split(train=tuple(range(a, b)), test=())])
    assert (fold.train.ranges, fold.train.days) == (
        ((_DAY0 + timedelta(days=a), _DAY0 + timedelta(days=b - 1)),),
        b - a,
    )


_FOLDS = st.lists(
    st.builds(
        lambda sharpe, ret: Fold(train=_side(0, None, None), test=_side(1, sharpe, ret)),
        st.one_of(st.none(), st.floats(min_value=-5, max_value=5, allow_nan=False)),
        st.one_of(st.none(), st.floats(min_value=-1, max_value=5, allow_nan=False)),
    ),
    max_size=20,
)


@given(folds=_FOLDS, data=st.data())
def test_the_summary_does_not_depend_on_fold_order(folds: list[Fold], data: st.DataObject) -> None:
    """Folds are a set of evidence: shuffling them changes no summary field."""
    shuffled = data.draw(st.permutations(folds))
    assert summarise_folds(shuffled) == summarise_folds(folds)


@given(folds=_FOLDS)
def test_the_summary_matches_its_definition(folds: list[Fold]) -> None:
    """Positive = test return > 0; median over the defined test Sharpes (statistics.median)."""
    sharpes = [f.test.sharpe for f in folds if f.test.sharpe is not None]
    positive = sum(1 for f in folds if f.test.total_return is not None and f.test.total_return > 0)
    summary = summarise_folds(folds)
    assert (summary.folds, summary.positive_test_folds, summary.median_test_sharpe) == (
        len(folds),
        positive,
        statistics.median(sharpes) if sharpes else None,
    )
