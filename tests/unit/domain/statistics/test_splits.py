"""Unit tests for walk-forward and purged CPCV splits (pre-registration §8.3)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.statistics.splits import purged_cpcv_splits, walk_forward_splits


class TestWalkForward:
    """Expanding window, 6-month step, 3-month test."""

    def test_three_years_gives_four_splits(self) -> None:
        """1096 days: 365-day warm-up, 183-day steps, 91-day tests; four windows fit."""
        splits = walk_forward_splits(1096)
        starts = [s.test[0] for s in splits]
        assert starts == [365, 548, 731, 914]
        assert all(len(s.test) == 91 for s in splits)

    def test_training_always_ends_where_testing_starts(self) -> None:
        """Expanding window: train is every day before the test window."""
        for split in walk_forward_splits(1500):
            assert split.train == tuple(range(split.test[0]))

    def test_too_short_gives_no_split(self) -> None:
        """Less than 12 months plus one test window yields nothing."""
        assert walk_forward_splits(400) == []

    def test_non_positive_months_raise(self) -> None:
        """Month arguments must be positive."""
        with pytest.raises(ValueError, match="positive"):
            walk_forward_splits(1000, step_months=0)


class TestPurgedCpcv:
    """5 groups, 2 held out, 5-day purge, 3-day embargo."""

    def test_ten_splits(self) -> None:
        """C(5, 2) = 10 combinations."""
        assert len(purged_cpcv_splits(1000)) == 10

    def test_purge_and_embargo_gaps(self) -> None:
        """Holding out the middle group drops 5 days before it and 3 after it."""
        split = next(s for s in purged_cpcv_splits(100, test_groups=1) if s.test[0] == 40)
        assert split.test == tuple(range(40, 60))
        assert split.train == (*range(35), *range(63, 100))

    # SEV-1: leakage from test into training invalidates every §8.3 result
    @given(
        n=st.integers(min_value=50, max_value=600),
        groups=st.integers(min_value=3, max_value=8),
        data=st.data(),
    )
    def test_train_never_touches_test_or_its_gaps(
        self, n: int, groups: int, data: st.DataObject
    ) -> None:
        """No training day lies within 5 days before or 3 days after a test day."""
        k = data.draw(st.integers(min_value=1, max_value=groups - 1))
        for split in purged_cpcv_splits(n, groups=groups, test_groups=k):
            test = set(split.test)
            for day in split.train:
                assert not any(day + d in test for d in range(1, 6))
                assert not any(day - d in test for d in range(4))

    @pytest.mark.parametrize("kwargs", [{"test_groups": 5}, {"test_groups": 0}, {"purge_days": -1}])
    def test_invalid_arguments_raise(self, kwargs: dict[str, int]) -> None:
        """Impossible group counts and negative gaps are caller errors."""
        with pytest.raises(ValueError, match=r"need|must"):
            purged_cpcv_splits(100, **kwargs)
