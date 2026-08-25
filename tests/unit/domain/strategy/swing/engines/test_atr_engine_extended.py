"""Extended unit tests for AtrEngine — additional boundary and regression checks.

Reference: spec-5-swing-strategy-adapter change.md.
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.strategy.swing.engines.atr import AtrEngine
from tests.unit.domain.strategy.swing.conftest import make_bars


class TestAtrLargeSequences:
    """ATR on sequences larger than period*2+1 (uses bounded window)."""

    def test_atr_period_14_with_50_bars(self) -> None:
        """ATR(14) should compute without error on 50 bars."""
        bars = make_bars(
            [100 + (i % 10) for i in range(50)],
            highs=[105 + (i % 10) for i in range(50)],
            lows=[95 + (i % 10) for i in range(50)],
        )
        result = AtrEngine.value(bars, period=14)
        assert result is not None, "ATR(14) must compute on 50 bars"
        assert result > Decimal("0"), f"ATR(14) on 50 bars must be > 0, got {result}"

    def test_atr_period_22_with_100_bars(self) -> None:
        """ATR(22) should compute without error on 100 bars (Chandelier period)."""
        bars = make_bars(
            [1000 + i for i in range(100)],
            highs=[1010 + i for i in range(100)],
            lows=[990 + i for i in range(100)],
        )
        result = AtrEngine.value(bars, period=22)
        assert result is not None, "ATR(22) must compute on 100 bars"
        assert result > Decimal("0"), f"ATR(22) on 100 bars must be > 0, got {result}"

    def test_atr_stable_flat_market(self) -> None:
        """ATR should remain approximately constant for flat bar sequences."""
        # All bars identical → TR = H-L = constant; ATR should equal H-L
        h_l = Decimal("10")
        bars = make_bars(
            [100] * 30,
            highs=[105] * 30,
            lows=[95] * 30,
        )
        result = AtrEngine.value(bars, period=14)
        assert result is not None, "ATR(14) must compute on 30 flat bars"
        assert abs(result - h_l) < Decimal("0.01"), (
            f"ATR on flat bars with H-L=10 should be ≈10, got {result:.4f}"
        )
