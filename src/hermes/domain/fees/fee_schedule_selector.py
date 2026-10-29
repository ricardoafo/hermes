"""Point-in-time fee-schedule selector — pick the right VIP0 row for (timestamp, pair).

Given a set of :class:`FeeScheduleRow` records loaded from
``docs/experiments/swing-mvp1/fee-history.csv``, :func:`select_fee_schedule`
returns the :class:`FeeSchedule` in force at a given UTC timestamp for a
given trading pair.

The Binance VIP0 rate is 10 bps maker / 10 bps taker for the entire
pre-registration backtest window (2020-09 → 2026-12) EXCEPT the
2022-07-28 → 2023-03-21 zero-fee BTC promotional window, during which
BTC pairs charge 0 bps. ETH/SOL pairs are unaffected by that promo.

Pair-partitioning is encoded in the CSV's ``tier`` column:

- ``VIP0``          — applies to every pair (catch-all baseline).
- ``VIP0_ETH_SOL``  — applies only to ETH/SOL pairs (during the promo,
                       preserves the standard 10/10 while BTC is 0/0).
- ``VIP0_BTCBUSD``  — informational only (BUSD phased out).

Domain purity: this module imports only :mod:`hermes.domain.fees.fee_schedule`,
:mod:`hermes.domain.value_objects`, :mod:`hermes.domain.errors`, and stdlib.
No I/O, no clock, no pydantic. The adapter-layer loader
(:mod:`hermes.adapters.fees.binance_history`) handles CSV parsing.

Reference:
    - openspec/archive/fee-history-time-aware-wire/change.md
    - docs/architecture/adr-011-paper-trading-engine.md D-09
    - docs/experiments/swing-mvp1/pre-registration-v1.md §6.1
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from hermes.domain.errors import DomainError
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.value_objects.pair import Pair

__all__ = ["FeeHistoryOutOfRangeError", "FeeScheduleRow", "select_fee_schedule"]


@dataclass(frozen=True, slots=True)
class FeeScheduleRow:
    """One row parsed from the point-in-time fee-history CSV.

    Attributes:
        effective_date: The UTC date on which this rate became active.
        tier: The tier label from the CSV (e.g. ``"VIP0"`` or
            ``"VIP0_ETH_SOL"``). Drives pair-partitioning — see the
            module docstring.
        maker_bps: Maker rate in basis points (0 during the promo).
        taker_bps: Taker rate in basis points.
    """

    effective_date: date
    tier: str
    maker_bps: int
    taker_bps: int


@dataclass(frozen=True, slots=True)
class FeeHistoryOutOfRangeError(DomainError):
    """Requested timestamp precedes every applicable row in the fee history.

    Raised by :func:`select_fee_schedule` when no row in the given
    history applies to the requested (timestamp, pair). This is an
    invariant violation from the caller's perspective — a live backtest
    should never step to a timestamp before the first fee row.

    Args:
        detail: Human-readable message including pair + timestamp.

    Reference: code-conventions.md §2 (Result/exception hybrid).
    """

    detail: str

    def __str__(self) -> str:
        """Return human-readable description."""
        return f"Fee history out of range: {self.detail}"


def _applies_to_pair(tier: str, pair: Pair) -> bool:
    """True iff the tier row applies to the given pair.

    See the module docstring for the tier-string convention.
    """
    if tier == "VIP0":
        # Catch-all baseline: applies to every pair. During the promo
        # this row carries 0/0 bps and is written to cover BTC; outside
        # the promo it carries 10/10 for all pairs.
        return True
    if tier == "VIP0_ETH_SOL":
        # Promo-window override for non-BTC pairs — the row that keeps
        # ETH and SOL at the standard 10/10 while BTC drops to 0/0.
        return pair.base.upper() in ("ETH", "SOL")
    return False


def _is_promo_window(at: date) -> bool:
    """True iff ``at`` is inside the Binance zero-fee BTC promotional window.

    The window runs 2022-07-28 through 2023-03-21 (inclusive).
    """
    return date(2022, 7, 28) <= at <= date(2023, 3, 21)


def select_fee_schedule(rows: Sequence[FeeScheduleRow], at: datetime, pair: Pair) -> FeeSchedule:
    """Return the fee schedule in force at ``at`` for ``pair``.

    Selects the most-recent row whose ``effective_date <= at.date()`` and
    whose ``tier`` applies to ``pair``. During the 2022-07-28 →
    2023-03-21 promo window, non-BTC pairs (ETH, SOL) are pinned to the
    ``VIP0_ETH_SOL`` row so they stay at the standard 10/10 bps even
    when ``VIP0`` is 0/0.

    Args:
        rows: Fee-history rows loaded from the CSV.
        at: The UTC timestamp of the trade (or bar close).
        pair: The trading pair being priced.

    Returns:
        A :class:`FeeSchedule` with a single :class:`FeeTier` at
        volume_threshold=0 (VIP0 is the volume-zero tier).

    Raises:
        FeeHistoryOutOfRangeError: When no applicable row's ``effective_date``
            precedes ``at``. A live backtest MUST NOT step past this
            error — the caller has hit a data-integrity boundary.
    """
    at_date = at.date()

    # ETH/SOL during the promo window MUST resolve to the VIP0_ETH_SOL
    # override row, not the catch-all VIP0 (which is 0/0 for BTC). Any
    # other pair, or ETH/SOL outside the promo, falls through to the
    # standard "most-recent VIP0 wins" selection.
    prefer_eth_sol_override = _is_promo_window(at_date) and pair.base.upper() in ("ETH", "SOL")

    def _row_applies(r: FeeScheduleRow) -> bool:
        if r.effective_date > at_date:
            return False
        if prefer_eth_sol_override:
            return r.tier == "VIP0_ETH_SOL"
        return _applies_to_pair(r.tier, pair)

    applicable = [r for r in rows if _row_applies(r)]
    if not applicable:
        raise FeeHistoryOutOfRangeError(f"No row applies to {pair} at {at.isoformat()}")

    latest = max(applicable, key=lambda r: r.effective_date)
    return FeeSchedule(
        pair=pair,
        tiers=(
            FeeTier(
                volume_threshold_usd=Decimal("0"),
                maker_bps=latest.maker_bps,
                taker_bps=latest.taker_bps,
            ),
        ),
    )
