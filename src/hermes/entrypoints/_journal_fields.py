"""Fields serve adds to its ``serve.bar.processed`` journal line.

Split out of :mod:`hermes.entrypoints.serve` unchanged, so serve stays under
its size ratchet (#1201).
"""

from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.ports.strategy import StrategyPort

__all__ = ["entry_check_fields"]


def entry_check_fields(strategy: StrategyPort) -> dict[str, str]:
    """Return which entry conditions held on the bar just evaluated, as yes/no.

    Booleans only, never a price or an indicator value, so the journal line
    stays outcome-blind for the reviews that read it (ADR-012 D-06, #656).
    Empty when the bar was not an entry check (a daily bar, or a position open).
    """
    if not isinstance(strategy, SwingStrategyAdapter):
        return {}
    check = strategy.last_entry_check
    if check is None:
        return {}
    return {
        "entry_breakout": _yes_no(check.breakout),
        "entry_momentum": _yes_no(check.momentum),
        "entry_regime": _yes_no(check.regime),
    }


def _yes_no(held: bool) -> str:
    return "yes" if held else "no"
