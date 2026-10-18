"""Unit tests for the §8.3 fold evidence of a run (issue #1181).

Invariants (pre-registration v1 §8.3, kept by v2; openspec 354 D-5;
openspec/changes/1181-fold-evidence/change.md):

- The folds are the signed ones: walk-forward with a 12-month warm-up, a
  6-month step and 3-month tests; purged CPCV with 5 groups, 2 held out,
  a 5-day purge and a 3-day embargo.
- ``daily_equity[i]`` is the equity at the end of ``start_date + i``, so the
  first return is earned on ``start_date + 1``; fold dates follow.
- A series too short for a scheme reports no fold for it and never refuses
  the run.

Every curve is synthetic. Expected values are worked out in the comments.
"""

import math
from datetime import date

import pytest

from hermes.application.backtest.fold_evidence import fold_evidence

_START = date(2020, 12, 31)
"""The first equity point's day; the first return is earned on 1 Jan 2021."""


def _equity(returns: list[float]) -> list[float]:
    points = [5000.0]
    for r in returns:
        points.append(points[-1] * (1.0 + r))
    return points


def _alternating(days: int) -> list[float]:
    """0.001 on even days, 0.003 on odd days (day 0 is 1 Jan 2021)."""
    return [0.002 + (0.001 if i % 2 else -0.001) for i in range(days)]


def _scheme(body: dict[str, object], name: str) -> dict[str, object]:
    scheme = body[name]
    assert isinstance(scheme, dict)
    return scheme


def _folds(body: dict[str, object], name: str) -> list[dict[str, dict[str, object]]]:
    folds = _scheme(body, name)["folds"]
    assert isinstance(folds, list)
    return folds


def test_the_schemes_are_the_signed_ones() -> None:
    """§8.3 and D-5: walk-forward 12/6/3 months; CPCV 5 groups, 2 held out, purge 5, embargo 3."""
    body = fold_evidence(_START, _equity(_alternating(10)))
    settings = {
        name: {k: v for k, v in _scheme(body, name).items() if k not in ("folds", "summary")}
        for name in ("walk_forward", "purged_cpcv")
    }
    assert settings == {
        "walk_forward": {"min_train_months": 12, "step_months": 6, "test_months": 3},
        "purged_cpcv": {"groups": 5, "test_groups": 2, "purge_days": 5, "embargo_days": 3},
    }


class TestWalkForward:
    """456 returns: a 365-day warm-up plus one 91-day test window fit exactly."""

    def test_the_first_test_window_starts_after_a_year(self) -> None:
        """Days 365-455: 1 Jan 2022 to 1 Apr 2022 (91 days); train is all of 2021."""
        (fold,) = _folds(fold_evidence(_START, _equity(_alternating(456))), "walk_forward")
        assert (fold["train"]["ranges"], fold["test"]["ranges"]) == (
            [["2021-01-01", "2021-12-31"]],
            [["2022-01-01", "2022-04-01"]],
        )

    def test_one_day_short_of_a_test_window_gives_no_fold(self) -> None:
        """455 returns: the 91-day window would end one day past the series."""
        assert _folds(fold_evidence(_START, _equity(_alternating(455))), "walk_forward") == []

    def test_the_test_metrics_are_the_fixed_rule_on_the_window(self) -> None:
        """Days 365-455 start on an odd day: 46 days of 0.003 and 45 of 0.001.

        Mean (46 * 0.003 + 45 * 0.001) / 91; sample variance over 90; return
        1.003^46 * 1.001^45 - 1. The rule is not refitted: the window is read
        off the one run.
        """
        (fold,) = _folds(fold_evidence(_START, _equity(_alternating(456))), "walk_forward")
        mean = (46 * 0.003 + 45 * 0.001) / 91
        variance = (46 * (0.003 - mean) ** 2 + 45 * (0.001 - mean) ** 2) / 90
        assert (fold["test"]["days"], fold["test"]["sharpe"], fold["test"]["total_return"]) == (
            91,
            pytest.approx(mean / math.sqrt(variance), rel=1e-9),
            pytest.approx(1.003**46 * 1.001**45 - 1, rel=1e-9),
        )


class TestPurgedCpcv:
    """50 returns in 5 groups of 10 days from 1 Jan 2021."""

    def test_every_pair_of_groups_is_held_out_once(self) -> None:
        """C(5, 2) = 10 folds in pair order; adjacent groups make one range.

        Groups: 1-10 Jan, 11-20 Jan, 21-30 Jan, 31 Jan-9 Feb, 10-19 Feb.
        """
        g = [
            ["2021-01-01", "2021-01-10"],
            ["2021-01-11", "2021-01-20"],
            ["2021-01-21", "2021-01-30"],
            ["2021-01-31", "2021-02-09"],
            ["2021-02-10", "2021-02-19"],
        ]
        folds = _folds(fold_evidence(_START, _equity(_alternating(50))), "purged_cpcv")
        assert [f["test"]["ranges"] for f in folds] == [
            [["2021-01-01", "2021-01-20"]],
            [g[0], g[2]],
            [g[0], g[3]],
            [g[0], g[4]],
            [["2021-01-11", "2021-01-30"]],
            [g[1], g[3]],
            [g[1], g[4]],
            [["2021-01-21", "2021-02-09"]],
            [g[2], g[4]],
            [["2021-01-31", "2021-02-19"]],
        ]

    def test_holding_out_groups_0_and_2_purges_and_embargoes_both(self) -> None:
        """Groups 0 and 2 (1-10 Jan, 21-30 Jan) leave 19 training days around the gaps.

        Embargo drops 11-13 Jan, purge 16-20 Jan, embargo 31 Jan-2 Feb: train is
        14-15 Jan and 3-19 Feb.
        """
        folds = _folds(fold_evidence(_START, _equity(_alternating(50))), "purged_cpcv")
        fold = folds[1]
        assert (fold["test"]["ranges"], fold["train"]["ranges"], fold["train"]["days"]) == (
            [["2021-01-01", "2021-01-10"], ["2021-01-21", "2021-01-30"]],
            [["2021-01-14", "2021-01-15"], ["2021-02-03", "2021-02-19"]],
            19,
        )

    def test_five_days_are_enough_for_the_folds(self) -> None:
        """Five returns are one day per group (1-5 Jan): ten folds, first 1-2 Jan, last 4-5 Jan."""
        folds = _folds(fold_evidence(_START, _equity(_alternating(5))), "purged_cpcv")
        assert (len(folds), folds[0]["test"]["ranges"], folds[-1]["test"]["ranges"]) == (
            10,
            [["2021-01-01", "2021-01-02"]],
            [["2021-01-04", "2021-01-05"]],
        )

    def test_four_days_give_no_fold_and_no_error(self) -> None:
        """Four returns cannot make five groups: no fold, the run is not refused."""
        assert _folds(fold_evidence(_START, _equity(_alternating(4))), "purged_cpcv") == []


def test_the_summary_counts_the_money_making_test_windows() -> None:
    """Every test side is two 10-day groups starting on even days: ten 0.001 and ten 0.003.

    Mean 0.002, sample deviation 0.001 * sqrt(20 / 19), so every fold's Sharpe
    and the median are 2 * sqrt(19 / 20); all ten made money.
    """
    summary = _scheme(fold_evidence(_START, _equity(_alternating(50))), "purged_cpcv")["summary"]
    assert summary == {
        "folds": 10,
        "positive_test_folds": 10,
        "median_test_sharpe": pytest.approx(2 * math.sqrt(19 / 20), rel=1e-9),
    }


def test_a_single_point_curve_reports_no_fold() -> None:
    """One equity point has no return: both schemes are empty, nothing is refused."""
    body = fold_evidence(_START, [5000.0])
    assert (_folds(body, "walk_forward"), _folds(body, "purged_cpcv")) == ([], [])
