"""Keep a strategy's position in step with the portfolio after a signal.

A strategy changes its own position when it emits a signal, before the
executor has tried to fill it: an ENTRY marks it long, an EXIT marks it flat.
When the executor does not fill the signal, the portfolio is unchanged, so the
strategy is put back to the position the portfolio holds:

- an ENTRY not filled (a risk rule, the exposure cap, a kill halt or the fill
  model refused it) leaves the portfolio flat, so the strategy is flat again,
  as if the entry had never fired. Otherwise it would later signal an exit for
  a position that does not exist, and could not enter that pair again (#707).
- an EXIT not filled (no book, the fill model, a kill halt) leaves the coin
  held, so the strategy is long again with the position's original entry time
  and price. Its exit rules then run on the next bar as before. Otherwise no
  exit rule would watch the coin any more (#706). An exit for a pair the
  portfolio does not hold leaves the strategy flat.

Serve and the backtest replay both call :func:`sync_after_signal`, so the two
paths keep one rule (ADR-011). Each looks the held position up from its own
records: serve from the tracker and the ledger, the replay from the kernel's
tracker and the entries it filled.

This is state bookkeeping only: no entry or exit condition, stop, sizing, fee
or risk rule changes (pre-registration §11, engine bug fix).
"""

from typing import TYPE_CHECKING

from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.ports.strategy import StrategyPort

if TYPE_CHECKING:
    from collections.abc import Callable

    from hermes.domain.strategy.signal import OpenPosition

__all__ = ["sync_after_signal"]


def sync_after_signal(
    strategy: StrategyPort,
    signal: StrategySignal,
    *,
    filled: bool,
    held: Callable[[], OpenPosition | None],
) -> None:
    """Return ``strategy`` to the position the portfolio holds after an unfilled signal.

    Args:
        strategy: The strategy that emitted ``signal`` for one pair.
        signal: The signal just handed to the executor.
        filled: Whether the portfolio now reflects the signal (a fill, or a
            trade already recorded for it).
        held: Looks up the position the portfolio still holds in the
            signal's pair, or ``None`` when it holds none. Called only for an
            EXIT that was not filled.
    """
    if filled:
        return
    if signal.kind is SignalKind.ENTRY:
        strategy.restore_position(None)
        return
    strategy.restore_position(held())
