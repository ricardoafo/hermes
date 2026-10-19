"""Tests for :class:`FeeTier` and :class:`FeeSchedule`.

Every test protects one invariant of the domain fee-schedule contract:

- FeeTier rejects negative thresholds and negative bps values.
- FeeTier.maker_rate / taker_rate translate bps to Decimal fractions.
- FeeSchedule rejects empty tiers.
- FeeSchedule requires strictly ascending thresholds when constructed
  directly; from_tiers() normalises any input order into canonical
  ascending order.
- FeeSchedule.fee_for_volume returns the highest-threshold tier whose
  threshold is <= volume; below any threshold, the standard tier applies.
- Property: schedules with random tier orderings from from_tiers() are
  always normalised (round-trip through shuffled inputs → same canonical
  tuple).

Reference: docs/guides/ai-authored-tests.md,
docs/decisions/2026-08-24-kraken-public-adapter.md.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.fees import FeeSchedule, FeeTier
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

# The Currency enum on this repo does not include USD — BTC/USDT is the
# canonical test pair for a USD-quoted market shape.
_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)


class TestFeeTierValidation:
    """FeeTier construction rejects impossible values."""

    def test_negative_volume_threshold_raises(self) -> None:
        """A negative threshold is not physically representable in a fee table."""
        with pytest.raises(ValueError, match="volume_threshold_usd must be >= 0"):
            FeeTier(volume_threshold_usd=Decimal("-1"), maker_bps=10, taker_bps=20)

    def test_negative_maker_bps_raises(self) -> None:
        """A negative maker fee cannot exist — every exchange bounds at zero."""
        with pytest.raises(ValueError, match="maker_bps must be >= 0"):
            FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=-1, taker_bps=10)

    def test_negative_taker_bps_raises(self) -> None:
        """A negative taker fee cannot exist — every exchange bounds at zero."""
        with pytest.raises(ValueError, match="taker_bps must be >= 0"):
            FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=10, taker_bps=-1)

    def test_zero_bps_accepted(self) -> None:
        """A zero-fee tier is valid — some venues zero out maker fees at high volumes."""
        tier = FeeTier(volume_threshold_usd=Decimal("1000000"), maker_bps=0, taker_bps=0)
        assert tier.maker_rate() == Decimal("0")
        assert tier.taker_rate() == Decimal("0")

    def test_maker_rate_converts_16_bps_to_fraction(self) -> None:
        """16 bps must translate to Decimal("0.0016") — no float rounding."""
        tier = FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26)
        assert tier.maker_rate() == Decimal("0.0016")

    def test_taker_rate_converts_26_bps_to_fraction(self) -> None:
        """26 bps must translate to Decimal("0.0026") — no float rounding."""
        tier = FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26)
        assert tier.taker_rate() == Decimal("0.0026")


class TestFeeScheduleValidation:
    """FeeSchedule construction enforces canonical ordering."""

    def test_empty_tiers_raises(self) -> None:
        """A schedule with no tiers cannot answer any fee query — rejected at construction."""
        with pytest.raises(ValueError, match="at least one tier"):
            FeeSchedule(pair=_BTC_USDT, tiers=())

    def test_out_of_order_tiers_raises_at_direct_construction(self) -> None:
        """Direct constructor rejects out-of-order tiers; callers must use from_tiers()."""
        higher = FeeTier(volume_threshold_usd=Decimal("50000"), maker_bps=14, taker_bps=24)
        lower = FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26)
        with pytest.raises(ValueError, match="strictly ascending"):
            FeeSchedule(pair=_BTC_USDT, tiers=(higher, lower))

    def test_duplicate_threshold_raises(self) -> None:
        """Two rows at the same threshold are ambiguous — rejected."""
        a = FeeTier(volume_threshold_usd=Decimal("50000"), maker_bps=14, taker_bps=24)
        b = FeeTier(volume_threshold_usd=Decimal("50000"), maker_bps=10, taker_bps=20)
        with pytest.raises(ValueError, match="strictly ascending"):
            FeeSchedule(pair=_BTC_USDT, tiers=(a, b))

    def test_from_tiers_normalises_reversed_input(self) -> None:
        """from_tiers(reversed) produces the same tuple as from_tiers(sorted)."""
        t0 = FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26)
        t1 = FeeTier(volume_threshold_usd=Decimal("50000"), maker_bps=14, taker_bps=24)
        t2 = FeeTier(volume_threshold_usd=Decimal("100000"), maker_bps=12, taker_bps=22)
        sorted_schedule = FeeSchedule.from_tiers(_BTC_USDT, (t0, t1, t2))
        reversed_schedule = FeeSchedule.from_tiers(_BTC_USDT, (t2, t1, t0))
        assert sorted_schedule.tiers == reversed_schedule.tiers


class TestFeeForVolume:
    """fee_for_volume returns the active tier's rate for a given trailing volume."""

    def _schedule(self) -> FeeSchedule:
        """Build a canonical three-tier schedule for BTC/USDT."""
        return FeeSchedule.from_tiers(
            _BTC_USDT,
            (
                FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=16, taker_bps=26),
                FeeTier(volume_threshold_usd=Decimal("50000"), maker_bps=14, taker_bps=24),
                FeeTier(volume_threshold_usd=Decimal("100000"), maker_bps=12, taker_bps=22),
            ),
        )

    def test_zero_volume_returns_standard_tier_maker(self) -> None:
        """At zero volume, the lowest-threshold tier applies (maker side)."""
        schedule = self._schedule()
        assert schedule.fee_for_volume(Decimal("0"), is_maker=True) == Decimal("0.0016")

    def test_zero_volume_returns_standard_tier_taker(self) -> None:
        """At zero volume, the lowest-threshold tier applies (taker side)."""
        schedule = self._schedule()
        assert schedule.fee_for_volume(Decimal("0"), is_maker=False) == Decimal("0.0026")

    def test_volume_at_mid_threshold_returns_that_tier(self) -> None:
        """Volume exactly at 50_000 promotes to the 14 bps maker tier."""
        schedule = self._schedule()
        assert schedule.fee_for_volume(Decimal("50000"), is_maker=True) == Decimal("0.0014")

    def test_volume_between_thresholds_returns_lower_tier(self) -> None:
        """Volume between 50k and 100k stays on the 14 bps maker tier."""
        schedule = self._schedule()
        assert schedule.fee_for_volume(Decimal("75000"), is_maker=True) == Decimal("0.0014")

    def test_volume_above_top_threshold_returns_top_tier(self) -> None:
        """Volume above the highest threshold returns the top-tier maker rate."""
        schedule = self._schedule()
        assert schedule.fee_for_volume(Decimal("500000"), is_maker=True) == Decimal("0.0012")

    def test_negative_volume_raises(self) -> None:
        """Negative trailing volume is impossible — surfaces as ValueError immediately."""
        schedule = self._schedule()
        with pytest.raises(ValueError, match="volume_usd must be >= 0"):
            schedule.fee_for_volume(Decimal("-1"), is_maker=True)


class TestFromTiersProperty:
    """Property: from_tiers(shuffled) always yields the canonical tuple."""

    @settings(max_examples=50)
    @given(
        thresholds=st.lists(
            st.integers(min_value=0, max_value=10_000_000),
            min_size=1,
            max_size=8,
            unique=True,
        ),
        data=st.data(),
    )
    def test_from_tiers_shuffle_invariant(self, thresholds: list[int], data: st.DataObject) -> None:
        """Reordering the input tuple never changes from_tiers()'s output.

        Uses hypothesis's built-in :func:`permutations` strategy to reorder
        the tiers deterministically — no ``random`` module (which would
        trip ruff S311 without giving us anything hypothesis does not).
        """
        tiers = tuple(
            FeeTier(volume_threshold_usd=Decimal(t), maker_bps=16, taker_bps=26) for t in thresholds
        )
        shuffled = data.draw(st.permutations(list(tiers)))
        canonical = FeeSchedule.from_tiers(_BTC_USDT, tiers)
        shuffled_schedule = FeeSchedule.from_tiers(_BTC_USDT, tuple(shuffled))
        assert canonical.tiers == shuffled_schedule.tiers
        # Also assert canonical monotonicity — the whole point of the schedule.
        for prev, curr in zip(canonical.tiers, canonical.tiers[1:], strict=False):
            assert prev.volume_threshold_usd < curr.volume_threshold_usd
