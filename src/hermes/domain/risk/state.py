"""Shared domain state types for risk overlay rules.

``PortfolioState`` carries the positions, correlation matrix, and drawdown
data that cross-portfolio rules need. ``MarketSnapshot`` carries the
market-microstructure data (spreads, orderbook age, macro price, peg data)
that individual-signal rules need.

Both are immutable dataclasses (frozen, slots) per code-conventions.md §1
(Value Object). They are deliberately thin — the real calculators live in
Spec 3 (OrderRouter); these types are the data contract the rules consume.

Reference: SA-9 §5.5 — risk overlay inputs.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from hermes.domain.value_objects.pair import Pair

__all__ = [
    "MarketSnapshot",
    "PortfolioState",
]


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Snapshot of the current portfolio for risk evaluation.

    Args:
        peak_value: Highest portfolio value recorded since inception
            (or since last reset). Used to compute drawdown in RO-08.
        current_value: Current mark-to-market portfolio value.
        realized_slippage_cumulative: Sum of realized slippage across all
            fills since the last reset. Decimal fraction (e.g. ``0.015``
            for 1.5%). Used by RO-03 cumulative check.
        correlations: Mapping of ``(pair_a, pair_b)`` → 30-day rolling
            Pearson correlation coefficient. Only populated pairs are
            stored; absent pairs are treated as uncorrelated (coeff = 0).
            RO-01 reads this.
        dd_halt_required: Set to ``True`` by portfolio-state calculators
            when the drawdown threshold has been breached and the halt
            state must be persisted. RO-08 surfaces this flag to
            KillTriggerEvaluator (Spec 9, #227).
        held_pairs: Pairs with an open position. RO-01 needs them: the
            signed lock looks at the correlation between two open pairs.
    """

    peak_value: Decimal
    current_value: Decimal
    realized_slippage_cumulative: Decimal
    correlations: dict[tuple[Pair, Pair], float] = field(default_factory=dict)
    dd_halt_required: bool = False
    held_pairs: frozenset[Pair] = frozenset()

    @property
    def drawdown(self) -> Decimal:
        """Drawdown fraction from peak — always ≥ 0.

        Returns:
            ``(peak - current) / peak`` clamped to ``[0, 1]``. Returns
            ``Decimal("0")`` when peak is zero (no trades yet) to avoid
            division-by-zero in rule code.
        """
        if self.peak_value == Decimal("0"):
            return Decimal("0")
        raw = (self.peak_value - self.current_value) / self.peak_value
        return max(Decimal("0"), raw)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Snapshot of market-microstructure data for risk evaluation.

    Args:
        pair: The trading pair this snapshot describes.
        current_spread: Current bid-ask spread as a Decimal fraction
            (e.g. ``0.002`` for 0.2% spread). Used by RO-02.
        median_spread_1h: 1-hour rolling median spread (same units as
            ``current_spread``). Used by RO-02 (10x check).
        orderbook_age_seconds: Age of the most recent orderbook snapshot
            in seconds. Used by RO-04 (> 2s → HALT).
        orderbook_timestamp: Wall-clock UTC timestamp when the orderbook
            was last received. Informational; age_seconds is the
            operative field for RO-04.
        realized_slippage_last: Slippage fraction for the most recent
            single fill (Decimal, e.g. ``0.006`` for 0.6%). Used by
            RO-03 single-fill check.
        stable_price: Current price of the stablecoin in USD. ``None``
            when the pair is not a stablecoin pair (rules RO-05, RO-06
            skip evaluation). E.g. ``Decimal("0.985")``.
        btc_log_return_1d: BTC 1-day log-return. Negative values indicate
            drawdown. Used by RO-07 (< -0.10 → HALT). ``None`` when BTC
            macro data is unavailable.
        candidate_pair: The pair the strategy intends to enter next.
            Used by RO-01 to look up correlation in the portfolio state.
    """

    pair: Pair
    current_spread: Decimal
    median_spread_1h: Decimal
    orderbook_age_seconds: float
    orderbook_timestamp: datetime
    realized_slippage_last: Decimal
    stable_price: Decimal | None
    btc_log_return_1d: float | None
    candidate_pair: Pair | None = None
