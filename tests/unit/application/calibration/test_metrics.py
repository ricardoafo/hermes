"""Unit tests for calibration metric calculators M-01..M-07.

Tests cover each calculator with hand-computed fixtures and boundary conditions.
All assertions carry informative failure messages per project convention.

Test coverage:
  - M-01: fill count — empty, single, multiple
  - M-02: fill-price delta bps — at-mid (zero), above-mid (positive slippage),
           below-mid (improvement), SELL side sign flip, multi-fill percentiles
  - M-03: latency — backtest mode always 0
  - M-04: rejection rate — zero attempts, all fills, all rejections, mixed
  - M-05: orphan positions — no orphans, one orphan, multiple orphans
  - M-06: regime returns — single regime, all None regime, mixed
  - M-07: queue quality — always None for MVP1

Reference: spec-6-calibration-harness/change.md R-1..R-5.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.calibration.metrics import (
    FillPriceDeltaBps,
    RegimeLabel,
    RegimeReturns,
    compute_m01_fill_count,
    compute_m02_fill_price_delta_bps,
    compute_m03_latency_ms,
    compute_m04_rejection_rate,
    compute_m05_orphan_positions,
    compute_m06_regime_returns,
    compute_m07_queue_quality,
)
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


def _make_order(
    side: OrderSide = OrderSide.BUY,
    qty: str = "0.1",
) -> Order:
    """Build a minimal Order for testing."""
    return Order(
        side=side,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal(qty), asset=Currency.BTC),
    )


def _make_fill(
    avg_price: str,
    side: OrderSide = OrderSide.BUY,
    qty: str = "0.1",
    timestamp: datetime = _NOW,
) -> Fill:
    """Build a Fill with the given avg_price."""
    order = _make_order(side=side, qty=qty)
    return Fill(
        order=order,
        executed_qty=Decimal(qty),
        avg_price=Decimal(avg_price),
        fee=Money(amount=Decimal("0.01"), currency=Currency.USDT),
        timestamp=timestamp,
    )


def _make_rejection(reason: str = "insufficient liquidity") -> Rejection:
    """Build a Rejection for testing."""
    order = _make_order()
    return Rejection(order=order, reason=reason)


# ---------------------------------------------------------------------------
# M-01: Total fill count
# ---------------------------------------------------------------------------


class TestM01FillCount:
    """M-01: compute_m01_fill_count — basic event count."""

    def test_empty_fills_returns_zero(self) -> None:
        """Empty fills sequence must return 0 — no events to count."""
        result = compute_m01_fill_count([])
        assert result == 0, (
            f"M-01: empty fills must return 0, got {result}. "
            "fill_count is a non-negative integer count of Fill events."
        )

    def test_single_fill_returns_one(self) -> None:
        """A single Fill must return count of 1."""
        fill = _make_fill("50000")
        result = compute_m01_fill_count([fill])
        assert result == 1, f"M-01: single fill must return 1, got {result}."

    def test_multiple_fills_returns_correct_count(self) -> None:
        """Three fills must return 3 — count is length of sequence."""
        fills = [_make_fill("50000"), _make_fill("51000"), _make_fill("49000")]
        result = compute_m01_fill_count(fills)
        assert result == 3, f"M-01: 3 fills must return 3, got {result}."

    def test_return_type_is_int(self) -> None:
        """Return type must be int, not float or Decimal."""
        result = compute_m01_fill_count([_make_fill("50000")])
        assert isinstance(result, int), (
            f"M-01: return type must be int, got {type(result).__name__}."
        )


# ---------------------------------------------------------------------------
# M-02: Fill-price delta bps
# ---------------------------------------------------------------------------


class TestM02FillPriceDeltaBps:
    """M-02: compute_m02_fill_price_delta_bps — primary calibration metric."""

    def test_empty_fills_returns_all_zero(self) -> None:
        """Empty fills must return FillPriceDeltaBps(0, 0, 0, 0)."""
        result = compute_m02_fill_price_delta_bps([], [])
        assert result == FillPriceDeltaBps(
            avg_bps=Decimal("0"),
            p50_bps=Decimal("0"),
            p95_bps=Decimal("0"),
            p99_bps=Decimal("0"),
        ), f"M-02: empty fills must return all-zero FillPriceDeltaBps, got {result}."

    def test_fill_at_mid_price_zero_delta(self) -> None:
        """Fill at exactly the mid-price must produce 0 bps delta (R-2.3)."""
        mid = Decimal("50000")
        fill = _make_fill("50000")
        result = compute_m02_fill_price_delta_bps([fill], [mid])
        assert result.avg_bps == Decimal("0"), (
            f"M-02: fill at mid-price must produce 0 bps avg delta, got {result.avg_bps}. "
            "Mid-price fill = zero slippage."
        )
        assert result.p50_bps == Decimal("0"), (
            f"M-02: fill at mid-price must produce 0 bps p50, got {result.p50_bps}."
        )

    def test_buy_fill_above_mid_positive_delta(self) -> None:
        """BUY fill above mid (slippage) must produce positive bps delta.

        Hand-computed: fill=50100, mid=50000 → delta = (50100-50000)/50000*10000 = 20 bps.
        """
        mid = Decimal("50000")
        fill = _make_fill("50100", side=OrderSide.BUY)
        result = compute_m02_fill_price_delta_bps([fill], [mid])
        expected_bps = Decimal("20")
        assert result.avg_bps == expected_bps, (
            f"M-02: BUY fill 100 above mid on 50000 price must produce +20 bps, "
            f"got {result.avg_bps}. Hand-computed: (50100-50000)/50000*10000=20."
        )

    def test_buy_fill_below_mid_negative_delta(self) -> None:
        """BUY fill below mid (improvement) must produce negative bps delta.

        Hand-computed: fill=49900, mid=50000 → delta = (49900-50000)/50000*10000 = -20 bps.
        """
        mid = Decimal("50000")
        fill = _make_fill("49900", side=OrderSide.BUY)
        result = compute_m02_fill_price_delta_bps([fill], [mid])
        expected_bps = Decimal("-20")
        assert result.avg_bps == expected_bps, (
            f"M-02: BUY fill 100 below mid must produce -20 bps (improvement), "
            f"got {result.avg_bps}. Hand-computed: (49900-50000)/50000*10000=-20."
        )

    def test_sell_fill_below_mid_positive_delta(self) -> None:
        """SELL fill below mid = adverse slippage = positive bps delta (sign inversion).

        For SELL: delta = -(avg_price - mid) / mid * 10000.
        Hand-computed: fill=49900, mid=50000 → -(49900-50000)/50000*10000 = 20 bps.
        """
        mid = Decimal("50000")
        fill = _make_fill("49900", side=OrderSide.SELL)
        result = compute_m02_fill_price_delta_bps([fill], [mid])
        expected_bps = Decimal("20")
        assert result.avg_bps == expected_bps, (
            f"M-02: SELL fill 100 below mid must produce +20 bps (slippage for seller), "
            f"got {result.avg_bps}. Sign inversion: -(49900-50000)/50000*10000=+20."
        )

    def test_sell_fill_above_mid_negative_delta(self) -> None:
        """SELL fill above mid = price improvement for seller = negative bps.

        Hand-computed: fill=50100, mid=50000 → -(50100-50000)/50000*10000 = -20 bps.
        """
        mid = Decimal("50000")
        fill = _make_fill("50100", side=OrderSide.SELL)
        result = compute_m02_fill_price_delta_bps([fill], [mid])
        expected_bps = Decimal("-20")
        assert result.avg_bps == expected_bps, (
            f"M-02: SELL fill 100 above mid must produce -20 bps (improvement for seller), "
            f"got {result.avg_bps}. Sign inversion: -(50100-50000)/50000*10000=-20."
        )

    def test_length_mismatch_raises(self) -> None:
        """Mismatched fills and mid_prices lengths must raise ValueError."""
        with pytest.raises(ValueError, match="same length"):
            compute_m02_fill_price_delta_bps(
                [_make_fill("50000")],
                [Decimal("50000"), Decimal("51000")],
            )

    def test_zero_mid_price_raises(self) -> None:
        """Zero mid_price must raise ValueError — cannot divide by zero."""
        fill = _make_fill("50000")
        with pytest.raises(ValueError, match="mid_price"):
            compute_m02_fill_price_delta_bps([fill], [Decimal("0")])

    def test_multi_fill_percentiles_ordered(self) -> None:
        """With multiple fills at different bps, p50 <= p95 <= p99."""
        # 10 fills: bps range from 0 to 90 bps above mid
        mid = Decimal("50000")
        fills = [_make_fill(str(50000 + i * 100)) for i in range(10)]
        mids = [mid] * 10
        result = compute_m02_fill_price_delta_bps(fills, mids)
        assert result.p50_bps <= result.p95_bps <= result.p99_bps, (
            f"M-02: p50 <= p95 <= p99 must hold across all fills. "
            f"Got p50={result.p50_bps}, p95={result.p95_bps}, p99={result.p99_bps}. "
            "Percentiles are monotonically non-decreasing by definition."
        )

    def test_return_type_is_fill_price_delta_bps(self) -> None:
        """Return type must be FillPriceDeltaBps."""
        fill = _make_fill("50000")
        result = compute_m02_fill_price_delta_bps([fill], [Decimal("50000")])
        assert isinstance(result, FillPriceDeltaBps), (
            f"M-02: return type must be FillPriceDeltaBps, got {type(result).__name__}."
        )


# ---------------------------------------------------------------------------
# M-03: Latency simulation
# ---------------------------------------------------------------------------


class TestM03LatencyMs:
    """M-03: compute_m03_latency_ms — backtest mode always returns 0."""

    def test_backtest_mode_returns_zero(self) -> None:
        """Backtest mode (default) must return Decimal(0) — latency mocked per OD-2."""
        result = compute_m03_latency_ms([], backtest_mode=True)
        assert result == Decimal("0"), (
            f"M-03: backtest_mode=True must return Decimal('0'), got {result}. "
            "MVP1 backtest has no real wall-clock latency (OD-2)."
        )

    def test_backtest_mode_default(self) -> None:
        """Default mode (no argument) is backtest_mode=True — must return 0."""
        result = compute_m03_latency_ms([])
        assert result == Decimal("0"), (
            f"M-03: default mode must return Decimal('0'), got {result}. "
            "backtest_mode defaults to True."
        )

    def test_backtest_mode_with_fills_returns_zero(self) -> None:
        """Backtest mode returns 0 regardless of fill count — fills not measured."""
        fills = [_make_fill("50000"), _make_fill("51000")]
        result = compute_m03_latency_ms(fills, backtest_mode=True)
        assert result == Decimal("0"), (
            f"M-03: backtest_mode=True with fills must still return 0, got {result}. "
            "Fills are not used to compute latency in backtest mode."
        )

    def test_return_type_is_decimal(self) -> None:
        """Return type must be Decimal, not float or int."""
        result = compute_m03_latency_ms([])
        assert isinstance(result, Decimal), (
            f"M-03: return type must be Decimal, got {type(result).__name__}. "
            "Decimal arithmetic is the project standard per code-conventions.md §6."
        )


# ---------------------------------------------------------------------------
# M-04: Rejection rate
# ---------------------------------------------------------------------------


class TestM04RejectionRate:
    """M-04: compute_m04_rejection_rate — fraction of rejected fill attempts."""

    def test_no_attempts_returns_zero(self) -> None:
        """Zero fills + zero rejections must return 0 — no attempts means no rejections."""
        result = compute_m04_rejection_rate([], [])
        assert result == Decimal("0"), (
            f"M-04: no attempts must return 0.0 rejection rate, got {result}."
        )

    def test_all_fills_no_rejections_returns_zero(self) -> None:
        """All fills, no rejections must return 0 rejection rate."""
        fills = [_make_fill("50000"), _make_fill("51000")]
        result = compute_m04_rejection_rate(fills, [])
        assert result == Decimal("0"), (
            f"M-04: all-fills scenario must return 0 rejection rate, got {result}."
        )

    def test_all_rejections_no_fills_returns_one(self) -> None:
        """All rejections, no fills must return 1.0 (100% rejection)."""
        rejections = [_make_rejection(), _make_rejection("spread too wide")]
        result = compute_m04_rejection_rate([], rejections)
        assert result == Decimal("1"), (
            f"M-04: all-rejections scenario must return 1.0 rate, got {result}."
        )

    def test_mixed_fills_and_rejections(self) -> None:
        """1 fill + 1 rejection must return 0.5 rate.

        Hand-computed: 1 / (1 + 1) = 0.5.
        """
        fills = [_make_fill("50000")]
        rejections = [_make_rejection()]
        result = compute_m04_rejection_rate(fills, rejections)
        assert result == Decimal("0.5"), (
            f"M-04: 1 fill + 1 rejection must return 0.5, got {result}. "
            "Hand-computed: 1 / (1+1) = 0.5."
        )

    def test_three_fills_one_rejection(self) -> None:
        """3 fills + 1 rejection must return 0.25.

        Hand-computed: 1 / 4 = 0.25.
        """
        fills = [_make_fill("50000"), _make_fill("51000"), _make_fill("52000")]
        rejections = [_make_rejection()]
        result = compute_m04_rejection_rate(fills, rejections)
        assert result == Decimal("0.25"), (
            f"M-04: 3 fills + 1 rejection must return 0.25, got {result}. "
            "Hand-computed: 1 / 4 = 0.25."
        )

    def test_return_is_decimal_in_unit_interval(self) -> None:
        """Return value must be in [0, 1]."""
        fills = [_make_fill("50000")]
        rejections = [_make_rejection()]
        result = compute_m04_rejection_rate(fills, rejections)
        assert Decimal("0") <= result <= Decimal("1"), (
            f"M-04: rejection rate must be in [0, 1], got {result}."
        )


# ---------------------------------------------------------------------------
# M-05: Orphan position count
# ---------------------------------------------------------------------------


class TestM05OrphanPositions:
    """M-05: compute_m05_orphan_positions — SA-4 P0 guard."""

    def test_no_positions_no_orphans(self) -> None:
        """Zero open positions must return 0 orphan count."""
        result = compute_m05_orphan_positions([], [])
        assert result == 0, f"M-05: no open positions must return 0 orphans, got {result}."

    def test_all_positions_have_fills_zero_orphans(self) -> None:
        """All positions matched to fills must return 0 (SA-4 P0 satisfied)."""
        result = compute_m05_orphan_positions(
            ["order-1", "order-2"],
            ["order-1", "order-2", "order-3"],  # more fills than positions is fine
        )
        assert result == 0, (
            f"M-05: all positions matched to fills must return 0 orphans, got {result}. "
            "SA-4 P0 is satisfied when every position has a preceding fill."
        )

    def test_one_orphan_position(self) -> None:
        """One position without a matching fill must return 1 orphan."""
        result = compute_m05_orphan_positions(
            ["order-1", "orphan-99"],
            ["order-1"],
        )
        assert result == 1, (
            f"M-05: one position without fill must return 1 orphan, got {result}. "
            "SA-4 P0 violation: orphan-99 has no fill event."
        )

    def test_multiple_orphan_positions(self) -> None:
        """Two positions without fills must return 2 orphans."""
        result = compute_m05_orphan_positions(
            ["order-1", "orphan-2", "orphan-3"],
            ["order-1"],
        )
        assert result == 2, (
            f"M-05: two positions without fills must return 2 orphans, got {result}."
        )

    def test_no_fills_all_positions_are_orphans(self) -> None:
        """Zero fills with open positions means all are orphans."""
        result = compute_m05_orphan_positions(["order-1", "order-2"], [])
        assert result == 2, (
            f"M-05: zero fills with 2 positions must return 2 orphans, got {result}. "
            "No fills at all means no position can be traced to a fill."
        )

    def test_return_type_is_int(self) -> None:
        """Return type must be int."""
        result = compute_m05_orphan_positions([], [])
        assert isinstance(result, int), (
            f"M-05: return type must be int, got {type(result).__name__}."
        )


# ---------------------------------------------------------------------------
# M-06: Regime-tagged returns
# ---------------------------------------------------------------------------


class TestM06RegimeReturns:
    """M-06: compute_m06_regime_returns — returns per regime bucket."""

    def _minimal_inputs(
        self,
        fills: list[Fill],
        labels: list[RegimeLabel | None],
        entry_prices: list[str],
        exit_prices: list[str],
    ) -> RegimeReturns:
        """Call compute_m06_regime_returns with string prices converted to Decimal."""
        return compute_m06_regime_returns(
            fills,
            labels,
            [Decimal(p) for p in entry_prices],
            [Decimal(p) for p in exit_prices],
        )

    def test_all_none_labels_returns_all_none_buckets(self) -> None:
        """All None regime labels must produce None in all buckets."""
        fills = [_make_fill("50000"), _make_fill("51000")]
        result = self._minimal_inputs(
            fills,
            [None, None],
            ["50000", "51000"],
            ["50100", "51100"],
        )
        assert result == RegimeReturns(
            bull_quiet=None,
            bull_vol=None,
            bear_quiet=None,
            bear_vol=None,
        ), f"M-06: all None labels must produce all-None buckets, got {result}."

    def test_single_bull_quiet_fill(self) -> None:
        """One fill in bull_quiet with known return must populate only that bucket.

        Hand-computed: entry=50000, exit=51000 → return = (51000-50000)/50000 = 0.02.
        """
        fills = [_make_fill("50000")]
        result = self._minimal_inputs(
            fills,
            [RegimeLabel.BULL_QUIET],
            ["50000"],
            ["51000"],
        )
        expected = Decimal("0.02")
        assert result.bull_quiet == expected, (
            f"M-06: bull_quiet fill with 2% gain must produce 0.02, got {result.bull_quiet}. "
            "Hand-computed: (51000-50000)/50000=0.02."
        )
        assert result.bull_vol is None, (
            f"M-06: bull_vol must be None when no fills in that regime, got {result.bull_vol}."
        )
        assert result.bear_quiet is None, (
            f"M-06: bear_quiet must be None when no fills in that regime, got {result.bear_quiet}."
        )

    def test_multiple_fills_same_bucket_accumulates(self) -> None:
        """Two fills in the same regime must accumulate returns in that bucket.

        Hand-computed: fill1 return=0.01, fill2 return=0.01 → total=0.02.
        """
        fills = [_make_fill("50000"), _make_fill("50000")]
        result = self._minimal_inputs(
            fills,
            [RegimeLabel.BEAR_VOL, RegimeLabel.BEAR_VOL],
            ["50000", "50000"],
            ["50500", "50500"],
        )
        expected = Decimal("0.01") + Decimal("0.01")  # 0.02
        assert result.bear_vol == expected, (
            f"M-06: two bear_vol fills must accumulate to 0.02, got {result.bear_vol}. "
            "Each fill: (50500-50000)/50000=0.01, sum=0.02."
        )

    def test_length_mismatch_raises(self) -> None:
        """Mismatched sequence lengths must raise ValueError."""
        fills = [_make_fill("50000")]
        with pytest.raises(ValueError, match="same length"):
            compute_m06_regime_returns(
                fills,
                [RegimeLabel.BULL_QUIET],
                [Decimal("50000"), Decimal("51000")],  # wrong length
                [Decimal("51000")],
            )

    def test_zero_entry_price_raises(self) -> None:
        """Zero entry_price must raise ValueError — cannot compute return."""
        fills = [_make_fill("50000")]
        with pytest.raises(ValueError, match="entry_price"):
            compute_m06_regime_returns(
                fills,
                [RegimeLabel.BULL_QUIET],
                [Decimal("0")],
                [Decimal("50000")],
            )


# ---------------------------------------------------------------------------
# M-07: Queue-position quality (placeholder)
# ---------------------------------------------------------------------------


class TestM07QueueQuality:
    """M-07: compute_m07_queue_quality — always None for MVP1 (OD-2)."""

    def test_empty_fills_returns_none(self) -> None:
        """Empty fills must return None — MVP1 placeholder."""
        result = compute_m07_queue_quality([])
        assert result is None, (
            f"M-07: compute_m07_queue_quality must always return None for MVP1 (OD-2), "
            f"got {result!r}. Real implementation is Spec 6 follow-up."
        )
        # Companion: the return type annotation is Decimal | None; None must be the sentinel.
        assert not isinstance(result, Decimal), (
            f"M-07: return must NOT be a Decimal for MVP1 — must be None (OD-2), got {result!r}."
        )

    def test_with_fills_returns_none(self) -> None:
        """Fills present must still return None — stub ignores all inputs."""
        fills = [_make_fill("50000"), _make_fill("51000")]
        result = compute_m07_queue_quality(fills)
        assert result is None, (
            f"M-07: must return None regardless of fill count (MVP1 OD-2), got {result!r}."
        )
        # Companion: the return type annotation is Decimal | None; None must be the sentinel.
        assert not isinstance(result, Decimal), (
            f"M-07: 2-fill input must also return None, not a Decimal (MVP1 OD-2), got {result!r}."
        )
