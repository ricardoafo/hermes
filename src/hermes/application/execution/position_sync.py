"""Keep a strategy's position in step with the portfolio after a signal.

A strategy marks itself long when it emits an ENTRY, before the executor has
tried to fill it. When the entry is not filled (a risk rule, the exposure cap,
a kill halt or the fill model refused it) the portfolio holds nothing, so the
strategy is put back to flat, as if the entry had never fired. Otherwise it
would later signal an exit for a position that does not exist, and could not
enter that pair again until then (#707).

Serve and the backtest replay both call :func:`sync_after_signal`, so the two
paths keep one rule (ADR-011). Only the refused-entry case is handled; a
declined EXIT is left as it was.

This is state bookkeeping only: no entry or exit condition, stop, sizing, fee
or risk rule changes (pre-registration §11, engine bug fix).
"""

from __future__ import annotations

from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.ports.strategy import StrategyPort

__all__ = ["sync_after_signal"]


def sync_after_signal(strategy: StrategyPort, signal: StrategySignal, *, filled: bool) -> None:
    """Return ``strategy`` to flat when its ENTRY signal was not filled.

    Args:
        strategy: The strategy that emitted ``signal`` for one pair.
        signal: The signal just handed to the executor.
        filled: Whether the portfolio now reflects the signal (a fill, or a
            trade already recorded for it).
    """
    if filled or signal.kind is not SignalKind.ENTRY:
        return
    strategy.restore_position(None)
