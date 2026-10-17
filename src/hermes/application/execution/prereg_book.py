"""The pre-registration §6.2 execution book, shared by the backtest and serve.

The backtest has no recorded depth, and serve sometimes has no fresh live
book, so both build the book pre-registration §5.3 and §6.2 describe: a
symmetric synthetic book whose touch sits at the pre-registered execution
price.

- ENTRY: 5 bps above the signal bar close.
- EXIT on the chandelier stop, priced :attr:`ExitPricing.STOP_ORDER` (v1):
  15 bps below the price the stop fills at, the lower of the stop and the
  bar's open. A resting stop-market under a bar that opens below it fills from
  the open, never from the stop above it (#785).
- EXIT on the chandelier stop, priced :attr:`ExitPricing.BAR_CLOSE` (v2
  §5.3, §6.2): a market sale 5 bps below the close of the bar the stop fired on.
- Any other EXIT (time stop, regime break): 5 bps below the signal bar close.

The strategy config selects the pricing, so the backtest, serve's fallback
book and serve's drift model price all follow the strategy being run.

Depth per side is the bar's volume times ``depth_frac``. Serve also walks
every live fill's order against this book to measure its drift (change
``353-fill-kernel-in-serve`` D-4).

Reference: openspec/changes/backtest-driver-mvp1-phase2/change.md D-1;
openspec/changes/353-fill-kernel-in-serve/change.md D-2, D-4;
openspec/changes/v2-strategy-config/change.md D-1.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.synthetic_orderbook import synthesize_orderbook
from hermes.domain.strategy.signal import SignalKind, StrategySignal

__all__ = ["ExecutionModel", "ExitPricing"]

_STOP_EXIT_REASON = "chandelier"


class ExitPricing(StrEnum):
    """How a chandelier stop exit is priced; every other exit sells at the bar close.

    Attributes:
        STOP_ORDER: Pre-registration v1 §5.3: an on-exchange stop-market,
            filled at the lower of the stop and the bar's open, ``stop_bps``
            under it.
        BAR_CLOSE: Pre-registration v2 §5.3, §6.2: a market sale at the close
            of the bar whose low reached the stop, ``entry_bps`` under it,
            the same price as every other v2 exit.
    """

    STOP_ORDER = "stop_order"
    BAR_CLOSE = "bar_close"


@dataclass(frozen=True, slots=True)
class ExecutionModel:
    """Execution prices (pre-registration §6.2) and the synthetic depth.

    Attributes:
        entry_bps: Cost of a market order over the bar close (§6.2: 5).
        stop_bps: Cost of a stop exit under its fill price, the lower of the
            stop and the bar's open (§6.2: 15). Read only under
            :attr:`ExitPricing.STOP_ORDER`.
        depth_frac: Fraction of the bar's volume resting on each book side.
        exit_pricing: How a stop exit is priced; v1's stop-market unless the
            strategy config selects another.
    """

    entry_bps: Decimal = Decimal("5")
    stop_bps: Decimal = Decimal("15")
    depth_frac: Decimal = Decimal("0.30")
    exit_pricing: ExitPricing = ExitPricing.STOP_ORDER

    def reference(self, signal: StrategySignal, bar: Bar) -> tuple[Decimal, Decimal]:
        """Return the reference price and the bps the touch sits from it.

        Args:
            signal: The signal to execute.
            bar: The bar that produced ``signal``.

        Returns:
            ``(reference price, half-spread bps)``. The reference of a
            chandelier stop exit is ``min(stop, bar.open)`` (#785) under
            :attr:`ExitPricing.STOP_ORDER`, and ``bar.close`` under
            :attr:`ExitPricing.BAR_CLOSE`, like every other exit.
        """
        if (
            self.exit_pricing is ExitPricing.STOP_ORDER
            and signal.kind is SignalKind.EXIT
            and signal.exit_reason == _STOP_EXIT_REASON
        ):
            # §5.3 posts an on-exchange stop-market: a bar that gaps under it
            # triggers at the open, so the stop above the open is never reached.
            return min(signal.stop_price, bar.open), self.stop_bps
        return bar.close, self.entry_bps

    def book(self, signal: StrategySignal, bar: Bar) -> Orderbook | None:
        """Return the synthetic book for ``signal``, or ``None`` when the bar has no depth.

        Args:
            signal: The signal to execute.
            bar: The bar that produced ``signal``.

        Returns:
            A book stamped at ``bar.close_time``, or ``None`` for a bar whose
            volume is too small to build one.
        """
        reference, half_spread = self.reference(signal, bar)
        depth = bar.volume * self.depth_frac
        if depth <= 0 or reference <= 0:
            return None
        try:
            return synthesize_orderbook(bar.pair, reference, half_spread, depth, bar.close_time)
        except ValueError:
            return None
