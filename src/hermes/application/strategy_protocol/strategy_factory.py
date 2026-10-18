"""Build a strategy adapter from its config entry's signal kind.

Every place that trades or replays a strategy (serve, the parity check, the
backtest drivers) builds it here, from the ``signal:`` key of its
``config/strategies.yaml`` entry and a parameter mapping. An entry without the
key is today's Donchian breakout adapter, so v1 and v2 trade exactly as
signed. A research candidate (ADR-026) adds one kind and its module; its
parameter names are the ones its hypothesis card and batch use, so a neighbour
replay overrides them by name.

An unknown kind is refused, never replaced by the default: a typo would
otherwise trade v1's rule under a candidate's name.

Reference: ADR-026 §1; research funnel interface contract §2a; issue #1074;
openspec/archive/1074-strategy-factory/change.md.
"""

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.strategy.swing.daily_ma import DailyMaAdapter
from hermes.domain.strategy.swing.ensemble import BreakoutEnsembleAdapter
from hermes.domain.strategy.swing.pullback import PullbackAdapter
from hermes.domain.strategy.swing.trend_slope import TrendSlopeAdapter
from hermes.ports.strategy import StrategyPort

__all__ = [
    "BREAKOUT_ENSEMBLE",
    "DAILY_MA",
    "DONCHIAN_BREAKOUT",
    "DONCHIAN_SLOPE",
    "RSI_PULLBACK",
    "StrategyKind",
    "UnknownSignalError",
    "known_signals",
    "strategy_kind",
]

DONCHIAN_BREAKOUT: Final = "donchian_breakout"
"""The v1/v2 rule (pre-registration v1 §5): the kind of an entry with no ``signal:``."""


BREAKOUT_ENSEMBLE: Final = "breakout_ensemble"
"""Research batch swing-b1, candidate C1 (swing-b1-ensemble, issue #1063)."""


DAILY_MA: Final = "daily_ma"
"""Research batch swing-b1, candidate C2 (swing-b1-daily-ma, issue #1064)."""


DONCHIAN_SLOPE: Final = "donchian_slope"
"""Research batch swing-b1 C3: v2's rule plus a rising daily EMA(200) (issue #1065)."""


RSI_PULLBACK: Final = "rsi_pullback"
"""Research batch swing-b1 C4: a 4h RSI pullback inside a rising daily trend (issue #1066)."""


class UnknownSignalError(ValueError):
    """A ``signal:`` value that names no strategy kind."""


@dataclass(frozen=True, slots=True)
class StrategyKind[S]:
    """One signal kind: its name and the builder that makes a fresh adapter.

    Args:
        signal: The ``signal:`` value that selects it.
        build: The adapter class or function. Its keyword-only parameters,
            with their defaults, are the kind's parameters.
    """

    signal: str
    build: Callable[..., S]

    def default_parameters(self) -> dict[str, Any]:
        """Return every parameter of the kind at its default, as a new mapping.

        Returns:
            Parameter name to default value.
        """
        return {
            name: parameter.default
            for name, parameter in inspect.signature(self.build).parameters.items()
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        }

    def create(self, parameters: Mapping[str, Any] | None = None) -> S:
        """Return a new adapter built with ``parameters`` over the defaults.

        Args:
            parameters: Overrides by name; ``None`` builds the defaults.

        Returns:
            A fresh adapter, holding no bar history.

        Raises:
            ValueError: A parameter the kind does not take.
        """
        overrides = dict(parameters or {})
        unknown = sorted(set(overrides) - set(self.default_parameters()))
        if unknown:
            raise ValueError(f"signal {self.signal!r} takes no parameter {unknown}")
        return self.build(**overrides)


_KINDS: Final[Mapping[str, StrategyKind[StrategyPort]]] = MappingProxyType(
    {
        DONCHIAN_BREAKOUT: StrategyKind(DONCHIAN_BREAKOUT, SwingStrategyAdapter),
        BREAKOUT_ENSEMBLE: StrategyKind(BREAKOUT_ENSEMBLE, BreakoutEnsembleAdapter),
        DAILY_MA: StrategyKind(DAILY_MA, DailyMaAdapter),
        DONCHIAN_SLOPE: StrategyKind(DONCHIAN_SLOPE, TrendSlopeAdapter),
        RSI_PULLBACK: StrategyKind(RSI_PULLBACK, PullbackAdapter),
    }
)


def known_signals() -> tuple[str, ...]:
    """Return every ``signal:`` value, in registration order."""
    return tuple(_KINDS)


def strategy_kind(signal: str | None) -> StrategyKind[StrategyPort]:
    """Return the kind a config entry's ``signal:`` names.

    Args:
        signal: The entry's ``signal:`` value; ``None`` when it has none.

    Returns:
        The kind; the Donchian breakout when ``signal`` is ``None``.

    Raises:
        UnknownSignalError: ``signal`` names no kind.
    """
    name = DONCHIAN_BREAKOUT if signal is None else signal
    try:
        return _KINDS[name]
    except KeyError:
        raise UnknownSignalError(
            f"unknown signal {signal!r}; known: {', '.join(known_signals())}"
        ) from None
