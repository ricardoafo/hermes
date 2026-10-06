"""Tests for ``sync_after_signal``, the rule serve and the replay share (#707, #706).

Invariants protected here:

- an ENTRY that did not fill restores the strategy to flat, exactly once;
- an EXIT that did not fill restores the strategy to the position the
  portfolio still holds, with its original entry time and price, exactly once;
- an EXIT not filled for a pair the portfolio does not hold leaves it flat;
- a filled signal, ENTRY or EXIT, leaves the strategy's position untouched and
  never looks the held position up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.application.execution.position_sync import sync_after_signal
from hermes.domain.market_data.bar import Bar
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import OpenPosition, SignalKind, SignalSide, StrategySignal

_AT = datetime(2024, 1, 2, tzinfo=UTC)
_HELD = OpenPosition(entry_time=datetime(2024, 1, 1, 4, tzinfo=UTC), entry_price=Decimal("95"))


def _never() -> OpenPosition | None:
    raise AssertionError("the held position must not be looked up")


@dataclass
class _Strategy:
    """StrategyPort fake that records every restored position."""

    restored: list[OpenPosition | None] = field(default_factory=list)

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> StrategySignal | None:
        """Never signal."""
        return None

    def seed(self, bars: Sequence[Bar], open_position: OpenPosition | None = None) -> None:
        """Ignore the seed."""

    def restore_position(self, open_position: OpenPosition | None) -> None:
        """Record the restored position."""
        self.restored.append(open_position)


def _signal(kind: SignalKind) -> StrategySignal:
    return StrategySignal(
        kind=kind,
        side=SignalSide.LONG,
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        atr_context=Decimal("2"),
        signal_time=_AT,
        exit_reason="chandelier" if kind is SignalKind.EXIT else None,
    )


def test_unfilled_entry_restores_the_strategy_to_flat_once() -> None:
    """A refused entry gives exactly one restore, to flat (``None``)."""
    strategy = _Strategy()
    sync_after_signal(strategy, _signal(SignalKind.ENTRY), filled=False, held=_never)
    assert strategy.restored == [None]


def test_unfilled_exit_restores_the_held_position_once() -> None:
    """A declined exit puts the strategy back in the position still held (#706)."""
    strategy = _Strategy()
    sync_after_signal(strategy, _signal(SignalKind.EXIT), filled=False, held=lambda: _HELD)
    assert strategy.restored == [_HELD]


def test_unfilled_exit_for_a_pair_not_held_leaves_the_strategy_flat() -> None:
    """An exit for a position already closed restores flat, not a phantom position."""
    strategy = _Strategy()
    sync_after_signal(strategy, _signal(SignalKind.EXIT), filled=False, held=lambda: None)
    assert strategy.restored == [None]


@pytest.mark.parametrize("kind", [SignalKind.ENTRY, SignalKind.EXIT])
def test_filled_signals_restore_nothing(kind: SignalKind) -> None:
    """A filled signal already matches the portfolio, so the strategy is left as it is."""
    strategy = _Strategy()
    sync_after_signal(strategy, _signal(kind), filled=True, held=_never)
    assert strategy.restored == []
