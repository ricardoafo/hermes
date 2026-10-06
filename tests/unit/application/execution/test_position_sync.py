"""Tests for ``sync_after_signal``, the rule serve and the replay share (#707).

Invariants protected here:

- an ENTRY that did not fill restores the strategy to flat, exactly once;
- a filled ENTRY leaves the strategy's position untouched;
- an EXIT, filled or not, leaves it untouched (a declined exit is #706).
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
    sync_after_signal(strategy, _signal(SignalKind.ENTRY), filled=False)
    assert strategy.restored == [None]


@pytest.mark.parametrize(
    ("kind", "filled"),
    [(SignalKind.ENTRY, True), (SignalKind.EXIT, True), (SignalKind.EXIT, False)],
)
def test_filled_entries_and_all_exits_restore_nothing(kind: SignalKind, filled: bool) -> None:
    """Only the refused-entry case touches the strategy; exits stay as they were (#706)."""
    strategy = _Strategy()
    sync_after_signal(strategy, _signal(kind), filled=filled)
    assert strategy.restored == []
