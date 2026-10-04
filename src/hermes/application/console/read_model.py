"""The one contract every operator console surface reads from.

The CLI, the TUI and pulled Telegram replies call only this protocol. A
fixture adapter implements it today; a ledger-backed adapter replaces it view
by view as the underlying data lands. Implementations are read-only and never
take the serve lock.

Reference: openspec/changes/console-read-model/change.md (D-1, D-4).
"""

from __future__ import annotations

from typing import Protocol

from hermes.application.console.views import (
    ActivityDay,
    Heartbeat,
    ObservationWindow,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    ShadowTest,
)

__all__ = ["ConsoleReadModel"]


class ConsoleReadModel(Protocol):
    """Read-only source of console view objects."""

    @property
    def demo_scenario(self) -> str | None:
        """Return the fixture scenario name, or ``None`` for real data.

        Renderers print a DEMO DATA tag whenever this is set, so demo numbers
        can never pass for real ones on any surface.
        """
        ...

    def heartbeat(self) -> Heartbeat:
        """Return whether the bar loop is checking on schedule."""
        ...

    def portfolio(self, range_: PortfolioRange = PortfolioRange.TODAY) -> PortfolioSummary:
        """Return portfolio value and results, with the change over ``range_``."""
        ...

    def positions(self) -> tuple[PositionRow, ...]:
        """Return open positions, in display order."""
        ...

    def position(self, coin: str) -> PositionDetail | None:
        """Return the detail of the open position in ``coin``, or ``None``."""
        ...

    def activity(self, *, include_quiet: bool = False) -> tuple[ActivityDay, ...]:
        """Return activity grouped by local day, newest day first.

        Args:
            include_quiet: Also return bar checks and signals (debugging).
        """
        ...

    def shadow_test(self) -> ShadowTest:
        """Return the 90-day shadow test status."""
        ...

    def observation(self) -> ObservationWindow:
        """Return the 90-day observation window figures and kill-trigger states."""
        ...
