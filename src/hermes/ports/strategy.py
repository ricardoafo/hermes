"""StrategyPort — abstract Protocol for signal-generation adapters.

Defines the contract every strategy adapter must satisfy.  The port is a
pure ``@runtime_checkable`` Protocol following the same pattern as
``MarketDataPort`` and ``RiskOverlayPort``.

``on_bar`` is called by the application layer once per closed bar.  The
adapter decides internally which timeframe it cares about and returns a
``StrategySignal`` when an entry or exit condition fires, or ``None`` when
no action is required for this bar.

Reference: code-conventions.md §5 (Ports as Protocols).
spec-5-swing-strategy-adapter change.md D-1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from hermes.domain.market_data.bar import Bar
    from hermes.domain.risk.state import PortfolioState
    from hermes.domain.strategy.signal import StrategySignal

__all__ = ["StrategyPort"]


@runtime_checkable
class StrategyPort(Protocol):
    """Contract every signal-generation adapter satisfies.

    Adapters MUST:

    - be stateful — they accumulate bar history internally so callers do
      not need to manage per-strategy lookback buffers;
    - return ``None`` on bars where no condition fires (most bars);
    - return a :class:`~hermes.domain.strategy.signal.StrategySignal` on
      exactly one bar per event (entry OR exit — never both simultaneously);
    - never peek at future bars — the no-lookahead invariant is formally
      tested via property tests (spec-5 R-8).

    Reference: spec-5-swing-strategy-adapter change.md D-1.
    """

    def on_bar(
        self,
        bar: Bar,
        portfolio_state: PortfolioState,
    ) -> StrategySignal | None:
        """Process one closed bar and optionally emit a trading signal.

        Called once per closed bar by the application layer.  The adapter
        routes bars to the correct internal buffer (4h vs 1D) and evaluates
        its entry/exit conditions against the closed-bar history.

        Args:
            bar: A fully closed OHLCV bar.  May be any interval; the adapter
                decides which intervals it cares about.
            portfolio_state: Current portfolio snapshot used by the adapter to
                check position limits and correlation locks.

        Returns:
            A :class:`~hermes.domain.strategy.signal.StrategySignal` when an
            entry or exit condition fires, or ``None`` when this bar triggers
            no action.

        Note:
            Implementations MUST NOT read any bar with a ``close_time`` later
            than ``bar.close_time`` — this is the no-lookahead contract
            (spec-5 R-8, SA-4 P1).
        """
        ...  # pragma: no cover
