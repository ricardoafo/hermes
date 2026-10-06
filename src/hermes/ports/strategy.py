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
    from collections.abc import Sequence

    from hermes.domain.market_data.bar import Bar
    from hermes.domain.risk.state import PortfolioState
    from hermes.domain.strategy.signal import OpenPosition, StrategySignal

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

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Prime the adapter's history from closed bars without evaluating them.

        Called once at warm-up, before the first live bar. Unlike
        :meth:`on_bar` it never returns a signal and never opens a position
        by itself: a historical bar must not be able to emit an entry that
        was never traded.

        Args:
            bars: Closed bars in delivery order.
            open_position: The position already held, restored from the
                ledger, or ``None`` when flat.
        """
        ...  # pragma: no cover

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Set the adapter's position to the one the portfolio holds.

        Called after a signal the executor did not fill, so the adapter's
        view matches the portfolio again: flat after an unfilled entry
        (#707), the held position after an unfilled exit (#706). Touches no
        bar history and never returns a signal.

        Args:
            open_position: The position held, or ``None`` when flat.
        """
        ...  # pragma: no cover
