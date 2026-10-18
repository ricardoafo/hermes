"""Tests for the missed-bar budget (issue #1117, ADR-021 §2 Done rule 2).

Invariants protected here: the allowance is 1% of the bars the streams close in
90 days, rounded down; a budget is crossed only past the allowance, never at
it; what is left never goes below 0; the texts carry counts and no price.
"""

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.monitoring.missed_bar_budget import (
    MissedBarBudget,
    budget_crossed_report,
    missed_bar_allowance,
)
from hermes.domain.market_data.kline import KlineInterval

_H4 = KlineInterval.H4
_D1 = KlineInterval.D1


def test_three_pairs_on_4h_bars_may_miss_16_bars() -> None:
    """Invariant: 3 x 540 bars in 90 days is 1620; 1% is 16.2, rounded down to 16."""
    assert missed_bar_allowance([_H4, _H4, _H4]) == 16


def test_three_pairs_on_4h_and_daily_bars_may_miss_18_bars() -> None:
    """Invariant: 1620 + 3 x 90 = 1890 bars; 1% is 18.9, rounded down to 18."""
    assert missed_bar_allowance([_H4, _H4, _H4, _D1, _D1, _D1]) == 18


def test_one_daily_stream_may_miss_no_bar() -> None:
    """Invariant: 90 daily bars give 0.9, so a single daily shadow may miss none."""
    assert missed_bar_allowance([_D1]) == 0


@given(
    st.lists(st.sampled_from(list(KlineInterval)), min_size=1, max_size=12),
    st.integers(min_value=1, max_value=400),
)
def test_the_allowance_is_the_largest_whole_number_within_one_percent(
    intervals: list[KlineInterval], days: int
) -> None:
    """Invariant: allowance <= 1% of the bars < allowance + 1, for any streams and length."""
    per_day = {"1m": 1440, "5m": 288, "1h": 24, "4h": 6, "1d": 1}
    bars = days * sum(per_day[i.value] for i in intervals)
    allowance = missed_bar_allowance(intervals, days=days)
    assert allowance * 100 <= bars < (allowance + 1) * 100


def test_missing_exactly_the_allowance_is_not_crossed() -> None:
    """Invariant: Done allows "at most 1%", so the allowance itself is still within it."""
    budget = MissedBarBudget(allowed=18, missed=18)
    assert (budget.crossed, budget.left) == (False, 0)


def test_one_bar_past_the_allowance_is_crossed_and_nothing_is_left() -> None:
    """Invariant: past the allowance the budget is crossed and ``left`` stays at 0."""
    budget = MissedBarBudget(allowed=18, missed=19)
    assert (budget.crossed, budget.left) == (True, 0)


def test_the_summary_names_bars_used_and_left() -> None:
    """Invariant: the restart report line gives used, allowed and left."""
    assert MissedBarBudget(allowed=18, missed=6).summary() == (
        "missed-bar budget: 6 of 18 bars used, 12 left"
    )


def test_the_crossing_alert_names_the_counts_and_the_rule() -> None:
    """Invariant: the alert says what was crossed and that the run can no longer be Done."""
    assert budget_crossed_report(MissedBarBudget(allowed=18, missed=27)) == (
        "missed-bar budget crossed: 27 bars missed, 18 allowed over 90 days "
        "(ADR-021 §2 Done rule 2); this run can no longer be Done"
    )
