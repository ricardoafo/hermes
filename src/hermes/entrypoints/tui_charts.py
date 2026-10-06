"""Reusable console charts.

Display-only: charts draw values handed to them and never decide anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from textual_plotext import PlotextPlot


class PriceChart(PlotextPlot):
    """Line chart of a series of closing prices."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._closes: tuple[Decimal, ...] = ()

    @property
    def closes(self) -> tuple[Decimal, ...]:
        """The closes currently plotted, oldest first."""
        return self._closes

    def set_closes(self, closes: Sequence[Decimal]) -> None:
        """Replace the plotted series and redraw.

        Args:
            closes: Closing prices, oldest first. Converted to float only for
                drawing; the Decimal values are kept as given.
        """
        self._closes = tuple(closes)
        self.plt.clear_data()
        if self._closes:
            self.plt.plot([float(close) for close in self._closes])
        self.refresh()
