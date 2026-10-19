"""Reference and boundary tests for the calibration metrics and trial records (#613).

A mutation-testing run over ``hermes.domain.calibration`` showed which of
M-01 to M-07 and the trial-record checks the existing tests left unpinned:
the percentile interpolation and the single-fill shortcut in M-02, the M-02
mean, the M-06 sums, the unlabelled-fill skip and every length and range
check on both sides. Each test below pins one of those with a value worked
out by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal

import pytest

from hermes.domain.calibration.metrics import (
    FillPriceDeltaBps,
    RegimeLabel,
    RegimeReturns,
    compute_m02_fill_price_delta_bps,
    compute_m03_latency_ms,
    compute_m06_regime_returns,
)
from hermes.domain.calibration.trial_record import (
    DatasetRef,
    TrialRecord,
    TrialSummary,
    summarise_trials,
)
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW = datetime(2024, 1, 15, 12, tzinfo=UTC)


def _fill(avg_price: str, side: OrderSide = OrderSide.BUY) -> Fill:
    order = Order(
        side=side,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.1"), asset=Currency.BTC),
        strategy_id=StrategyId("test-strategy"),
    )
    return Fill(
        order=order,
        executed_qty=Decimal("0.1"),
        avg_price=Decimal(avg_price),
        fee=Money(amount=Decimal("0.01"), currency=Currency.USDT),
        timestamp=_NOW,
    )


_MID = Decimal("100")
_UNSORTED_PRICES = ("100.3", "100", "100.41", "100.1", "100.2")
"""BUY fills at mid 100: deltas 30, 0, 41, 10 and 20 bps, deliberately unsorted."""


class TestM02Percentiles:
    """M-02's mean and percentiles on five fills, worked by hand."""

    def _result(self) -> FillPriceDeltaBps:
        fills = [_fill(p) for p in _UNSORTED_PRICES]
        return compute_m02_fill_price_delta_bps(fills, [_MID] * len(fills))

    def test_mean_is_the_exact_average(self) -> None:
        """The deltas sum to 101 bps over 5 fills: a mean of 20.2, not a rounded 20."""
        assert self._result().avg_bps == Decimal("20.2")

    def test_median_is_the_middle_delta(self) -> None:
        """Sorted 0, 10, 20, 30, 41: the median is 20."""
        assert self._result().p50_bps == Decimal("20")

    def test_p95_interpolates_between_the_top_two(self) -> None:
        """Position 0.95 * 4 = 3.8: 30 + 0.8 * (41 - 30) = 38.8."""
        assert self._result().p95_bps == Decimal("38.8")

    def test_p99_interpolates_between_the_top_two(self) -> None:
        """Position 0.99 * 4 = 3.96: 30 + 0.96 * (41 - 30) = 40.56."""
        assert self._result().p99_bps == Decimal("40.56")

    def test_two_fills_interpolate_from_the_lowest(self) -> None:
        """Deltas 10 and 20: the median sits halfway between them, at 15."""
        fills = [_fill("100.1"), _fill("100.2")]
        assert compute_m02_fill_price_delta_bps(fills, [_MID] * 2).p50_bps == Decimal("15")

    def test_median_of_six_uses_the_next_neighbour(self) -> None:
        """Deltas 0, 10, 20, 35, 70, 80: position 2.5 sits between 20 and 35, at 27.5."""
        fills = [_fill(p) for p in ("100.8", "100", "100.35", "100.1", "100.7", "100.2")]
        assert compute_m02_fill_price_delta_bps(fills, [_MID] * 6).p50_bps == Decimal("27.5")

    def test_single_fill_percentile_is_the_exact_delta(self) -> None:
        """With one fill every percentile is its delta, unrounded: no float round trip."""
        result = compute_m02_fill_price_delta_bps([_fill("3.000001")], [Decimal("3")])
        exact = (Decimal("3.000001") - Decimal("3")) / Decimal("3") * Decimal("10000")
        assert (result.p50_bps, result.p95_bps, result.p99_bps) == (exact, exact, exact)

    def test_more_fills_than_mid_prices_is_rejected(self) -> None:
        """Every fill needs its own mid price."""
        with pytest.raises(ValueError, match="same length"):
            compute_m02_fill_price_delta_bps([_fill("100"), _fill("101")], [_MID])

    def test_negative_mid_price_is_rejected(self) -> None:
        """A mid price below zero cannot come from a real order book."""
        with pytest.raises(ValueError, match="mid_price must be > 0"):
            compute_m02_fill_price_delta_bps([_fill("100")], [Decimal("-1")])


class TestM03Signature:
    """M-03's mode flag is keyword-only."""

    def test_backtest_mode_is_keyword_only(self) -> None:
        """A positional mode flag is refused, so it cannot be passed by accident."""
        call: Callable[..., object] = compute_m03_latency_ms
        with pytest.raises(TypeError):
            call([], False)


def _m06(labels: list[RegimeLabel | None], entries: list[str], exits: list[str]) -> RegimeReturns:
    return compute_m06_regime_returns(
        [_fill("100") for _ in labels],
        labels,
        [Decimal(e) for e in entries],
        [Decimal(x) for x in exits],
    )


class TestM06RegimeReturns:
    """M-06 sums per-fill returns into the four regime buckets."""

    def test_unlabelled_fill_does_not_stop_the_scan(self) -> None:
        """A fill with no regime is skipped and the next labelled fill still counts."""
        result = _m06([None, RegimeLabel.BULL_QUIET], ["100", "100"], ["120", "110"])
        assert result == RegimeReturns(
            bull_quiet=Decimal("0.1"), bull_vol=None, bear_quiet=None, bear_vol=None
        )

    def test_losing_trade_is_negative(self) -> None:
        """Entry 100, exit 90: a return of -0.1."""
        result = _m06([RegimeLabel.BEAR_VOL], ["100"], ["90"])
        assert result.bear_vol == Decimal("-0.1")

    def test_returns_in_one_bucket_add_up(self) -> None:
        """100 to 110 is 0.1 and 200 to 210 is 0.05: the bucket holds 0.15."""
        result = _m06([RegimeLabel.BULL_VOL] * 2, ["100", "200"], ["110", "210"])
        assert result.bull_vol == Decimal("0.15")

    def test_negative_entry_price_is_rejected(self) -> None:
        """An entry price below zero is refused, not turned into a return."""
        with pytest.raises(ValueError, match="entry_price must be > 0"):
            _m06([RegimeLabel.BEAR_QUIET], ["-1"], ["10"])

    @pytest.mark.parametrize(
        ("fills", "labels", "entries", "exits"),
        [
            (1, 2, 1, 1),
            (1, 1, 2, 1),
            (1, 1, 1, 2),
            (2, 1, 2, 2),
            (2, 1, 1, 2),
            (1, 2, 2, 1),
            (1, 2, 2, 2),
            (2, 1, 1, 1),
        ],
    )
    def test_mismatched_lengths_are_rejected(
        self, fills: int, labels: int, entries: int, exits: int
    ) -> None:
        """Fills, labels, entries and exits must all be the same length, longer or shorter."""
        with pytest.raises(ValueError, match="same length"):
            compute_m06_regime_returns(
                [_fill("100")] * fills,
                [RegimeLabel.BULL_QUIET] * labels,
                [Decimal("100")] * entries,
                [Decimal("110")] * exits,
            )


class _NoOffset(tzinfo):
    """A tzinfo that claims a zone but reports no UTC offset."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "nowhere"


def _record(strategy_id: str = "s1") -> TrialRecord:
    return TrialRecord(
        recorded_at=datetime(2026, 10, 3, 14, tzinfo=UTC),
        strategy_id=strategy_id,
        parameters={"donchian_period": "20"},
        split=DataSplit.TRAIN,
        window_start=datetime(2024, 1, 1, tzinfo=UTC),
        window_end=datetime(2024, 3, 31, tzinfo=UTC),
        datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
        metrics={"fills_count": "8"},
        sharpe=None,
        git_sha="c" * 40,
        git_dirty=False,
    )


class TestTrialRecordChecks:
    """Trial-record validation at its edges."""

    def test_tzinfo_without_an_offset_is_rejected(self) -> None:
        """A tzinfo that reports no UTC offset is as unusable as a naive time."""
        with pytest.raises(ValueError, match="timezone-aware"):
            replace(_record(), window_start=datetime(2024, 1, 1, tzinfo=_NoOffset()))

    def test_empty_window_is_rejected(self) -> None:
        """A window that ends where it starts covers no time."""
        start = datetime(2024, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="precede"):
            replace(_record(), window_start=start, window_end=start)


class TestSummariseTrialsFilter:
    """Filtering the trial log by strategy."""

    def test_counts_only_the_named_strategy(self) -> None:
        """Two alpha, one beta and three gamma trials: filtering by beta counts one."""
        records = [_record("alpha")] * 2 + [_record("beta")] + [_record("gamma")] * 3
        assert summarise_trials(records, strategy_id="beta") == TrialSummary(
            count=1, validation_count=0, sharpe_variance=None
        )

    def test_strategy_filter_is_keyword_only(self) -> None:
        """A positional strategy id is refused, so it cannot be mistaken for the log."""
        call: Callable[..., object] = summarise_trials
        with pytest.raises(TypeError):
            call([_record()], "s1")
