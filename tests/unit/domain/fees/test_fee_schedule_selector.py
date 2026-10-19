"""Unit + property tests for the point-in-time fee-schedule selector.

Verifies the pre-registration v1 §6.1 fee model:

- 2020-09 → 2022-07-27: 10/10 bps for every pair.
- 2022-07-28 → 2023-03-21: 0/0 for BTC pairs, 10/10 for ETH/SOL
  (the Binance zero-fee BTC promotional window).
- 2023-03-22 onward: 10/10 for every pair.

The property test asserts idempotency within a tier's effective window.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.fees.fee_schedule_selector import (
    FeeHistoryOutOfRangeError,
    FeeScheduleRow,
    select_fee_schedule,
)
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair


def _pair(base: str, quote: str = "USDT") -> Pair:
    """Build a Pair value object from string base/quote."""
    return Pair(base=Currency(base), quote=Currency(quote))


# The full pre-registration v1 fee history, encoded as rows. Keep in
# sync with docs/experiments/swing-mvp1/fee-history.csv — the adapter
# loader test asserts the CSV → row conversion end-to-end.
FULL_ROWS: tuple[FeeScheduleRow, ...] = (
    FeeScheduleRow(effective_date=date(2017, 7, 14), tier="VIP0", maker_bps=10, taker_bps=10),
    FeeScheduleRow(effective_date=date(2018, 7, 14), tier="VIP0", maker_bps=10, taker_bps=10),
    FeeScheduleRow(effective_date=date(2019, 3, 22), tier="VIP0", maker_bps=10, taker_bps=10),
    FeeScheduleRow(effective_date=date(2020, 1, 1), tier="VIP0", maker_bps=10, taker_bps=10),
    FeeScheduleRow(effective_date=date(2022, 7, 28), tier="VIP0", maker_bps=0, taker_bps=0),
    FeeScheduleRow(
        effective_date=date(2022, 7, 28), tier="VIP0_ETH_SOL", maker_bps=10, taker_bps=10
    ),
    FeeScheduleRow(effective_date=date(2023, 3, 22), tier="VIP0", maker_bps=10, taker_bps=10),
    FeeScheduleRow(effective_date=date(2024, 1, 1), tier="VIP0", maker_bps=10, taker_bps=10),
    FeeScheduleRow(effective_date=date(2026, 8, 25), tier="VIP0", maker_bps=10, taker_bps=10),
)


# ---------------------------------------------------------------------------
# Unit tests — every acceptance criterion in the change spec.
# ---------------------------------------------------------------------------


def test_baseline_pre_promo_btc_returns_10_bps() -> None:
    """Pre-promo BTC/USDT trade sees VIP0 base 10/10 bps (A-1.1 equivalent)."""
    result = select_fee_schedule(FULL_ROWS, datetime(2020, 9, 1, tzinfo=UTC), _pair("BTC"))
    tier = result.tiers[0]
    assert tier.maker_bps == 10, f"pre-promo BTC maker: expected 10, got {tier.maker_bps}"
    assert tier.taker_bps == 10, f"pre-promo BTC taker: expected 10, got {tier.taker_bps}"


def test_promo_window_btc_returns_zero_bps() -> None:
    """During the 2022-07-28..2023-03-21 promo, BTC/USDT fees are 0/0 (A-1.2)."""
    result = select_fee_schedule(FULL_ROWS, datetime(2022, 10, 15, tzinfo=UTC), _pair("BTC"))
    tier = result.tiers[0]
    assert tier.maker_bps == 0, f"promo BTC maker: expected 0, got {tier.maker_bps}"
    assert tier.taker_bps == 0, f"promo BTC taker: expected 0, got {tier.taker_bps}"


def test_promo_window_eth_stays_10_bps() -> None:
    """ETH is unaffected by the BTC promo — still 10/10 (A-1.3)."""
    result = select_fee_schedule(FULL_ROWS, datetime(2022, 10, 15, tzinfo=UTC), _pair("ETH"))
    tier = result.tiers[0]
    assert tier.maker_bps == 10, f"promo ETH maker: expected 10, got {tier.maker_bps}"
    assert tier.taker_bps == 10, f"promo ETH taker: expected 10, got {tier.taker_bps}"


def test_promo_window_sol_stays_10_bps() -> None:
    """SOL is unaffected by the BTC promo — still 10/10 (A-1.3 companion)."""
    result = select_fee_schedule(FULL_ROWS, datetime(2022, 10, 15, tzinfo=UTC), _pair("SOL"))
    tier = result.tiers[0]
    assert tier.maker_bps == 10, f"promo SOL maker: expected 10, got {tier.maker_bps}"
    assert tier.taker_bps == 10, f"promo SOL taker: expected 10, got {tier.taker_bps}"


def test_post_promo_btc_returns_10_bps() -> None:
    """After 2023-03-22 BTC returns to 10/10 (A-1.4)."""
    result = select_fee_schedule(FULL_ROWS, datetime(2023, 6, 1, tzinfo=UTC), _pair("BTC"))
    tier = result.tiers[0]
    assert tier.maker_bps == 10, f"post-promo BTC maker: expected 10, got {tier.maker_bps}"
    assert tier.taker_bps == 10, f"post-promo BTC taker: expected 10, got {tier.taker_bps}"


def test_promo_boundary_first_day_btc_is_zero() -> None:
    """The promo starts on 2022-07-28 UTC. That day itself is inside the window."""
    result = select_fee_schedule(FULL_ROWS, datetime(2022, 7, 28, tzinfo=UTC), _pair("BTC"))
    tier = result.tiers[0]
    assert tier.maker_bps == 0, "first day of promo BTC must be 0 bps"


def test_promo_boundary_last_day_btc_is_zero() -> None:
    """The promo ends on 2023-03-21 inclusive; 2023-03-22 is post-promo."""
    result = select_fee_schedule(FULL_ROWS, datetime(2023, 3, 21, tzinfo=UTC), _pair("BTC"))
    tier = result.tiers[0]
    assert tier.maker_bps == 0, "last day of promo BTC must still be 0 bps"


def test_out_of_range_before_first_row_raises() -> None:
    """Timestamp before every row's effective_date raises FeeHistoryOutOfRangeError (A-2.1)."""
    with pytest.raises(FeeHistoryOutOfRangeError) as excinfo:
        select_fee_schedule(FULL_ROWS, datetime(2015, 1, 1, tzinfo=UTC), _pair("BTC"))
    msg = str(excinfo.value)
    assert "2015-01-01" in msg, f"error should mention timestamp, got: {msg}"


# ---------------------------------------------------------------------------
# Property tests — Hypothesis-driven invariants (R-4).
# ---------------------------------------------------------------------------


@given(
    days_after_2020_09_01=st.integers(min_value=0, max_value=365 * 6),  # 2020-09-01 .. ~2026-09
    base=st.sampled_from(["BTC", "ETH", "SOL"]),
)
@settings(max_examples=100, deadline=None)
def test_property_selection_deterministic(days_after_2020_09_01: int, base: str) -> None:
    """Same (timestamp, pair) always returns the same schedule (determinism)."""
    ts = datetime(2020, 9, 1, tzinfo=UTC) + timedelta(days=days_after_2020_09_01)
    pair = _pair(base)
    result_a = select_fee_schedule(FULL_ROWS, ts, pair)
    result_b = select_fee_schedule(FULL_ROWS, ts, pair)
    # Compare each attribute individually — specific enough that a
    # tier-mismatch surfaces which attribute drifted.
    assert result_a.pair == result_b.pair, (
        f"pair drift at ts={ts.isoformat()}: {result_a.pair} vs {result_b.pair}"
    )
    assert len(result_a.tiers) == len(result_b.tiers), (
        f"tier count drift at ts={ts.isoformat()}: {len(result_a.tiers)} vs {len(result_b.tiers)}"
    )
    assert result_a.tiers[0].maker_bps == result_b.tiers[0].maker_bps, (
        f"maker_bps drift at ts={ts.isoformat()} pair={pair}: "
        f"{result_a.tiers[0].maker_bps} vs {result_b.tiers[0].maker_bps}"
    )
    assert result_a.tiers[0].taker_bps == result_b.tiers[0].taker_bps, (
        f"taker_bps drift at ts={ts.isoformat()} pair={pair}: "
        f"{result_a.tiers[0].taker_bps} vs {result_b.tiers[0].taker_bps}"
    )


@given(
    day_1_offset=st.integers(min_value=0, max_value=200),  # both timestamps inside baseline
    day_2_offset=st.integers(min_value=0, max_value=200),
    base=st.sampled_from(["BTC", "ETH", "SOL"]),
)
@settings(max_examples=50, deadline=None)
def test_property_idempotent_inside_baseline_window(
    day_1_offset: int, day_2_offset: int, base: str
) -> None:
    """Any two timestamps in the pre-promo baseline give identical schedules.

    2020-09-01 to 2022-07-27 is entirely inside one tier's effective
    window (VIP0 at 10/10 bps for every pair). The selector MUST return
    the same schedule for any two timestamps inside this window.
    """
    baseline_start = datetime(2020, 9, 1, tzinfo=UTC)
    # Cap the offset so both timestamps stay strictly BEFORE the promo
    # window start (2022-07-28). max_days = (2022-07-28 - 2020-09-01) - 1 = 695.
    max_days = (date(2022, 7, 28) - date(2020, 9, 1)).days - 1
    day_1_offset = day_1_offset % max_days
    day_2_offset = day_2_offset % max_days
    ts_1 = baseline_start + timedelta(days=day_1_offset)
    ts_2 = baseline_start + timedelta(days=day_2_offset)
    pair = _pair(base)

    result_1 = select_fee_schedule(FULL_ROWS, ts_1, pair)
    result_2 = select_fee_schedule(FULL_ROWS, ts_2, pair)
    # Both baseline-window results MUST be exactly 10/10 bps VIP0.
    assert result_1.tiers[0].maker_bps == 10, (
        f"baseline maker at ts_1={ts_1.date()} pair={pair}: expected 10, "
        f"got {result_1.tiers[0].maker_bps}"
    )
    assert result_2.tiers[0].maker_bps == 10, (
        f"baseline maker at ts_2={ts_2.date()} pair={pair}: expected 10, "
        f"got {result_2.tiers[0].maker_bps}"
    )
    assert result_1.tiers[0].taker_bps == result_2.tiers[0].taker_bps, (
        f"taker drift inside baseline window: ts_1={ts_1.date()} "
        f"ts_2={ts_2.date()} pair={pair} → "
        f"{result_1.tiers[0].taker_bps} vs {result_2.tiers[0].taker_bps}"
    )


# ---------------------------------------------------------------------------
# Volume threshold sanity — the returned tier is VIP0 (zero-volume).
# ---------------------------------------------------------------------------


def test_returned_schedule_has_single_zero_volume_tier() -> None:
    """The point-in-time selector emits VIP0 → a single tier at volume=0."""
    result = select_fee_schedule(FULL_ROWS, datetime(2024, 1, 1, tzinfo=UTC), _pair("BTC"))
    assert len(result.tiers) == 1, f"expected 1 tier, got {len(result.tiers)}"
    assert result.tiers[0].volume_threshold_usd == Decimal("0"), (
        f"VIP0 threshold must be 0, got {result.tiers[0].volume_threshold_usd}"
    )
