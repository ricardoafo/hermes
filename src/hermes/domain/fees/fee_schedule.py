"""Fee-schedule value objects — volume-tiered maker/taker rates for a pair.

An exchange fee schedule is a small ordered list of ``FeeTier`` rows:
each row carries a 30-day quote-volume threshold (USD-equivalent for
Kraken; venues that publish in their own quote unit convert at
adapter-boundary time), a maker rate and a taker rate expressed in
basis points (1 bp = 0.01%).

The lowest-threshold row is the "standard" tier — the one applied when
the trader's 30-day volume is below every higher threshold.

Domain purity: this module imports only :mod:`hermes.domain.value_objects`
and stdlib. No pydantic, no httpx (enforced by import-linter contract
``domain-no-pydantic`` + ``domain-no-async-or-network``).

Reference: code-conventions.md §1 (Value Object) and §6 (Decimal for
ledger values).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from hermes.domain.value_objects.pair import Pair

__all__ = ["FeeSchedule", "FeeTier"]


# Basis-points denominator: 1 bp = 1/10000. Exposed as a module-level
# constant so downstream conversions (bps → fraction) never re-derive
# the magic number and every rounding path shares one denominator.
_BPS_PER_UNIT: Final[Decimal] = Decimal("10000")


@dataclass(frozen=True, slots=True)
class FeeTier:
    """One row of a volume-tiered exchange fee schedule.

    Attributes:
        volume_threshold_usd: The 30-day quote volume (USD-equivalent)
            at which this tier's fees become the active rate. The tier
            with the lowest threshold in a schedule is applied whenever
            trailing volume is below every higher threshold — i.e. the
            "standard" tier at zero volume.
        maker_bps: Basis-points fee for maker fills (resting orders that
            add liquidity). Must be >= 0.
        taker_bps: Basis-points fee for taker fills (marketable orders
            that remove liquidity). Must be >= 0.
    """

    volume_threshold_usd: Decimal
    maker_bps: int
    taker_bps: int

    def __post_init__(self) -> None:
        """Reject negative thresholds and negative bps values.

        Raises:
            ValueError: On ``volume_threshold_usd < 0``, ``maker_bps < 0``
                or ``taker_bps < 0`` — every fee-schedule row must be
                physically representable in an exchange fee table.
        """
        if self.volume_threshold_usd < Decimal("0"):
            raise ValueError(
                f"FeeTier.volume_threshold_usd must be >= 0, got {self.volume_threshold_usd}"
            )
        if self.maker_bps < 0:
            raise ValueError(f"FeeTier.maker_bps must be >= 0, got {self.maker_bps}")
        if self.taker_bps < 0:
            raise ValueError(f"FeeTier.taker_bps must be >= 0, got {self.taker_bps}")

    def maker_rate(self) -> Decimal:
        """Return the maker fee as a Decimal fraction (e.g. 16 bps → ``0.0016``)."""
        return Decimal(self.maker_bps) / _BPS_PER_UNIT

    def taker_rate(self) -> Decimal:
        """Return the taker fee as a Decimal fraction (e.g. 26 bps → ``0.0026``)."""
        return Decimal(self.taker_bps) / _BPS_PER_UNIT


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Volume-tiered fee schedule for a trading pair.

    Attributes:
        pair: The trading pair this schedule applies to.
        tiers: Tuple of :class:`FeeTier` rows, always sorted ascending by
            ``volume_threshold_usd``. Empty tuple is rejected — a schedule
            with no tiers cannot answer :meth:`fee_for_volume`.

    The constructor normalises input order — callers may pass tiers in
    any order and get a canonical schedule back (see :meth:`from_tiers`).
    Two schedules with the same tiers in different input orders compare
    equal.
    """

    pair: Pair
    tiers: tuple[FeeTier, ...]

    def __post_init__(self) -> None:
        """Reject an empty schedule and enforce ascending ``volume_threshold_usd`` order.

        Raises:
            ValueError: On empty ``tiers`` or duplicate ``volume_threshold_usd``
                values (two rows at the same threshold are ambiguous) or on
                out-of-order tiers (callers should use :meth:`from_tiers`
                which normalises input order rather than constructing
                :class:`FeeSchedule` directly with unsorted rows).
        """
        if not self.tiers:
            raise ValueError(f"FeeSchedule.tiers must contain at least one tier (pair={self.pair})")
        for prev, curr in zip(self.tiers, self.tiers[1:], strict=False):
            if curr.volume_threshold_usd <= prev.volume_threshold_usd:
                raise ValueError(
                    "FeeSchedule.tiers must be strictly ascending by "
                    f"volume_threshold_usd, got {prev.volume_threshold_usd} "
                    f"then {curr.volume_threshold_usd} (pair={self.pair}). "
                    "Use FeeSchedule.from_tiers() to normalise input order."
                )

    @classmethod
    def from_tiers(cls, pair: Pair, tiers: tuple[FeeTier, ...]) -> FeeSchedule:
        """Build a schedule from ``tiers`` in any order — normalises ascending.

        Args:
            pair: Trading pair.
            tiers: Tuple of :class:`FeeTier` rows in ANY order. The
                factory sorts ascending by ``volume_threshold_usd`` before
                constructing the schedule.

        Returns:
            A canonical :class:`FeeSchedule` — two calls with the same
            ``pair`` and the same set of tiers (in any order) return
            equal schedules.

        Raises:
            ValueError: Empty ``tiers`` or duplicate thresholds — see the
                constructor for the full contract.
        """
        sorted_tiers = tuple(sorted(tiers, key=lambda t: t.volume_threshold_usd))
        return cls(pair=pair, tiers=sorted_tiers)

    def fee_for_volume(self, volume_usd: Decimal, *, is_maker: bool) -> Decimal:
        """Return the fee rate (as Decimal fraction) for a given 30-day volume.

        The active tier is the highest-threshold row whose
        ``volume_threshold_usd`` is ``<= volume_usd``. When ``volume_usd``
        is below every threshold (e.g. below the standard tier's own
        threshold, which is rare — most schedules start at 0), the lowest
        tier applies.

        Args:
            volume_usd: The trader's trailing 30-day volume in USD (or
                the schedule's declared quote unit — adapters normalise
                to USD at wire-boundary time).
            is_maker: ``True`` to return the maker rate, ``False`` for
                the taker rate.

        Returns:
            The fee as a :class:`Decimal` fraction (e.g. ``Decimal("0.0016")``
            for 16 bps).

        Raises:
            ValueError: On negative ``volume_usd`` — trailing volume is a
                cumulative count and cannot be negative.
        """
        if volume_usd < Decimal("0"):
            raise ValueError(
                f"FeeSchedule.fee_for_volume: volume_usd must be >= 0, got {volume_usd}"
            )
        # Walk from the highest threshold down and return the first tier
        # whose threshold is <= volume_usd. Tiers are ascending, so
        # reversed(tiers) is descending.
        active: FeeTier = self.tiers[0]
        for tier in self.tiers:
            if tier.volume_threshold_usd <= volume_usd:
                active = tier
            else:
                break
        return active.maker_rate() if is_maker else active.taker_rate()
