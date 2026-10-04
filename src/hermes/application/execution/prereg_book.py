"""The pre-registration §6.2 execution book, shared by the backtest and serve.

The backtest has no recorded depth, and serve sometimes has no fresh live
book, so both build the book pre-registration §5.3 and §6.2 describe: a
symmetric synthetic book whose touch sits at the pre-registered execution
price.

- ENTRY: 5 bps above the signal bar close.
- EXIT on the chandelier stop: 15 bps below the stop trigger price.
- Any other EXIT (time stop, regime break): 5 bps below the signal bar close.

Depth per side is the bar's volume times ``depth_frac``. Serve also walks
every live fill's order against this book to measure its drift (change
``353-fill-kernel-in-serve`` D-4).

Reference: openspec/changes/backtest-driver-mvp1-phase2/change.md D-1;
openspec/changes/353-fill-kernel-in-serve/change.md D-2, D-4.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.synthetic_orderbook import synthesize_orderbook
from hermes.domain.strategy.signal import SignalKind, StrategySignal

__all__ = ["ExecutionModel"]

_STOP_EXIT_REASON = "chandelier"


@dataclass(frozen=True, slots=True)
class ExecutionModel:
    """Execution prices (pre-registration §6.2) and the synthetic depth.

    Attributes:
        entry_bps: Cost of a market order over the bar close (§6.2: 5).
        stop_bps: Cost of a stop exit over the stop trigger (§6.2: 15).
        depth_frac: Fraction of the bar's volume resting on each book side.
    """

    entry_bps: Decimal = Decimal("5")
    stop_bps: Decimal = Decimal("15")
    depth_frac: Decimal = Decimal("0.30")

    def reference(self, signal: StrategySignal, bar: Bar) -> tuple[Decimal, Decimal]:
        """Return the reference price and the bps the touch sits from it.

        Args:
            signal: The signal to execute.
            bar: The bar that produced ``signal``.

        Returns:
            ``(reference price, half-spread bps)``.
        """
        if signal.kind is SignalKind.EXIT and signal.exit_reason == _STOP_EXIT_REASON:
            return signal.stop_price, self.stop_bps
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
