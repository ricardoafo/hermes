"""BreakoutEnsembleAdapter: research batch swing-b1, candidate C1 (swing-b1-ensemble).

Entry (long only), on a 4h bar close, all true:
    1. For at least ``agree_min`` of the lookbacks {20, 55, ``longest_lookback``}
       4h bars, the close is above the highest high of the previous L bars.
    2. The last closed daily close is above the daily EMA(200).

Exits are v2's, first to fire (pre-registration-v2 §5.2): the chandelier stop
with ATR multiple ``chandelier_atr_mult``, posted at the entry fill and moved
once per UTC daily close, never down; the 90-day time stop; the regime break.
They are :class:`SwingStrategyAdapter`'s exits unchanged, so this adapter only
replaces the entry check.

The three parameters are the batch's free parameters, named as in its
``parameters`` block; their defaults are the signed base values.

Reference: research batch swing-b1 §C1 (ADR-026); card
``docs/experiments/swing-mvp1/cards/swing-b1-ensemble.md``; issue #1063.
"""

from datetime import datetime
from decimal import Decimal
from typing import Final

from hermes.domain.strategy.swing.adapter import EntryCheck, SwingStrategyAdapter
from hermes.domain.strategy.swing.engines.donchian import DonchianEngine

__all__ = ["FIXED_LOOKBACKS", "BreakoutEnsembleAdapter"]

FIXED_LOOKBACKS: Final = (20, 55)
"""The two lookbacks the card fixes; the third is ``longest_lookback``."""


class BreakoutEnsembleAdapter(SwingStrategyAdapter):
    """Enter when several Donchian breakout lengths agree; exit as v2.

    Args:
        agree_min: How many of the three lookbacks must break out (base 2).
        chandelier_atr_mult: The chandelier stop's ATR multiple (base 3.0).
        longest_lookback: The third, longest lookback in 4h bars (base 120).

    Raises:
        ValueError: ``agree_min`` is not 1, 2 or 3, or ``longest_lookback`` is
            not longer than 55.
    """

    def __init__(
        self,
        *,
        agree_min: int = 2,
        chandelier_atr_mult: Decimal = Decimal("3.0"),
        longest_lookback: int = 120,
    ) -> None:
        if not 1 <= agree_min <= len(FIXED_LOOKBACKS) + 1:
            raise ValueError(f"agree_min must be 1, 2 or 3, got {agree_min}")
        if longest_lookback <= FIXED_LOOKBACKS[-1]:
            raise ValueError(
                f"longest_lookback must be longer than {FIXED_LOOKBACKS[-1]}, "
                f"got {longest_lookback}"
            )
        super().__init__(chandelier_atr_mult=chandelier_atr_mult)
        self._agree_min = agree_min
        self._lookbacks = (*FIXED_LOOKBACKS, longest_lookback)

    def _entry_check(self, as_of: datetime) -> EntryCheck:
        """Evaluate the C1 entry rule; ``breakout`` is the agreement condition.

        C1 has no ADX filter, so ``momentum`` always holds and the journal's
        yes/no record names only the conditions C1 has.
        """
        agreeing = sum(
            DonchianEngine.high_break(self._bars_4h, period=lookback)
            for lookback in self._lookbacks
        )
        return EntryCheck(
            breakout=agreeing >= self._agree_min,
            momentum=True,
            regime=self._regime_above_ema(as_of),
        )
