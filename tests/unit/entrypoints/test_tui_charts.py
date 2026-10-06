"""PriceChart plots a series of Decimal closes (#732).

Invariants:

- the chart keeps the exact Decimal closes it was given, in order;
- the drawn plot spans the series low to high, so the axis shows both ends;
- an empty series draws an empty plot instead of raising.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from textual.app import App, ComposeResult

from hermes.entrypoints.tui_charts import PriceChart


class _ChartApp(App[None]):
    def compose(self) -> ComposeResult:
        yield PriceChart(id="chart")


async def _render(closes: list[Decimal]) -> tuple[tuple[Decimal, ...], str]:
    app = _ChartApp()
    async with app.run_test(size=(60, 16)) as pilot:
        chart = app.query_one(PriceChart)
        chart.set_closes(closes)
        await pilot.pause()
        return chart.closes, chart.plt.build()


def test_chart_keeps_decimal_closes_in_order() -> None:
    """Invariant: the widget stores the given closes unchanged and in order."""
    closes = [Decimal("100.5"), Decimal("101.25"), Decimal("99.75")]
    kept, _ = asyncio.run(_render(closes))
    assert kept == (Decimal("100.5"), Decimal("101.25"), Decimal("99.75"))


def test_chart_axis_spans_series_low_and_high() -> None:
    """Invariant: the drawn plot labels cover the lowest and highest close."""
    _, drawn = asyncio.run(_render([Decimal("100"), Decimal("110"), Decimal("105")]))
    assert "100" in drawn
    assert "110" in drawn


def test_chart_with_no_closes_is_empty_and_does_not_raise() -> None:
    """Invariant: an empty series leaves an empty chart, never an error."""
    kept, _ = asyncio.run(_render([]))
    assert kept == ()
