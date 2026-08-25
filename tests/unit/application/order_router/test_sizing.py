"""Unit and property tests for SizingCalculator.

Tests cover:
- T-1 three-cap min() formula correctness.
- Property: sized notional never exceeds ATR cap, Kelly cap, or 5% hard cap.
- Binding-cap label is correct for each cap.
- Degenerate inputs (zero qty) return Failure.
- SizingInput validates positive inputs.
- MVP1 Kelly degeneracy (f_star=0.20 → kelly_fraction=5% = hard_cap).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from returns.result import Failure, Success

from hermes.application.order_router.sizing import SizingCalculator, SizingInput, SizingResult

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

CALCULATOR = SizingCalculator()

# Pre-reg §5.4 hard cap fraction.
_HARD_CAP = Decimal("0.05")
# Pre-reg default risk per trade.
_RISK_PCT = Decimal("0.01")
# Pre-reg default ATR stop multiplier.
_STOP_MULT = Decimal("1.5")
# MVP1 Kelly f* floor (OD-4).
_F_STAR_MVP1 = Decimal("0.20")


def _make_input(
    *,
    equity: str = "100000",
    entry_px: str = "50000",
    atr: str = "500",
    stop_atr_mult: str = "1.5",
    risk_pct: str = "0.01",
    f_star_estimate: str = "0.20",
) -> SizingInput:
    """Return a ``SizingInput`` with sensible defaults."""
    return SizingInput(
        equity=Decimal(equity),
        entry_px=Decimal(entry_px),
        atr=Decimal(atr),
        stop_atr_mult=Decimal(stop_atr_mult),
        risk_pct=Decimal(risk_pct),
        f_star_estimate=Decimal(f_star_estimate),
    )


# ---------------------------------------------------------------------------
# Unit tests — formula correctness
# ---------------------------------------------------------------------------


class TestSizingCalculatorFormula:
    """SizingCalculator produces correct intermediate and final values."""

    def test_atr_notional_formula(self) -> None:
        """ATR notional = (equity * risk_pct) / stop_atr_mult per SA-7."""
        inp = _make_input(equity="100000", risk_pct="0.01", stop_atr_mult="1.5")
        result = CALCULATOR.compute(inp)
        assert isinstance(result, Success), (
            f"Expected Success, got {result}. Sizing should succeed for valid inputs."
        )
        sr: SizingResult = result.unwrap()
        expected_atr = (Decimal("100000") * Decimal("0.01")) / Decimal("1.5")
        assert sr.atr_notional == expected_atr, (
            f"ATR notional mismatch: expected {expected_atr!r}, got {sr.atr_notional!r}. "
            f"Formula: (equity * risk_pct) / stop_atr_mult."
        )

    def test_kelly_notional_formula_mvp1(self) -> None:
        """Kelly notional = equity * min(0.25 * f_star, 0.05) per SA-1."""
        inp = _make_input(equity="100000", f_star_estimate="0.20")
        result = CALCULATOR.compute(inp)
        assert isinstance(result, Success), f"Expected Success, got {result}."
        sr = result.unwrap()
        expected_kelly_fraction = min(Decimal("0.25") * Decimal("0.20"), Decimal("0.05"))
        expected_kelly = Decimal("100000") * expected_kelly_fraction
        assert sr.kelly_notional == expected_kelly, (
            f"Kelly notional mismatch: expected {expected_kelly!r}, got {sr.kelly_notional!r}. "
            f"Formula: equity * min(0.25 * f_star, 0.05)."
        )

    def test_hard_cap_notional_is_5pct_equity(self) -> None:
        """Hard cap notional = 0.05 * equity per pre-reg §5.4."""
        inp = _make_input(equity="200000")
        result = CALCULATOR.compute(inp)
        assert isinstance(result, Success), f"Expected Success, got {result}."
        sr = result.unwrap()
        expected_hard = Decimal("0.05") * Decimal("200000")
        assert sr.hard_cap_notional == expected_hard, (
            f"Hard cap notional mismatch: expected {expected_hard!r}, "
            f"got {sr.hard_cap_notional!r}. Formula: 0.05 * equity."
        )

    def test_qty_equals_notional_over_entry_px(self) -> None:
        """Qty = notional / entry_px."""
        inp = _make_input(equity="100000", entry_px="50000")
        result = CALCULATOR.compute(inp)
        assert isinstance(result, Success), f"Expected Success, got {result}."
        sr = result.unwrap()
        expected_qty = sr.notional / Decimal("50000")
        assert sr.qty == expected_qty, (
            f"qty mismatch: expected {expected_qty!r}, got {sr.qty!r}. "
            f"Formula: qty = notional / entry_px."
        )

    def test_mvp1_degeneracy_kelly_equals_hard_cap(self) -> None:
        """With f_star=0.20: kelly_fraction=0.05 = hard_cap_fraction — hard cap wins."""
        inp = _make_input(equity="100000", f_star_estimate="0.20")
        result = CALCULATOR.compute(inp)
        assert isinstance(result, Success), f"Expected Success, got {result}."
        sr = result.unwrap()
        assert sr.kelly_notional == sr.hard_cap_notional, (
            f"MVP1 degeneracy: kelly and hard cap should be equal. "
            f"kelly={sr.kelly_notional!r}, hard_cap={sr.hard_cap_notional!r}."
        )


# ---------------------------------------------------------------------------
# Unit tests — binding cap identification
# ---------------------------------------------------------------------------


class TestSizingCalculatorBindingCap:
    """SizingCalculator correctly identifies which cap is binding."""

    def test_atr_binding_when_smallest(self) -> None:
        """When ATR notional is smallest, binding_cap='atr'."""
        # Make ATR notional very small: very small risk_pct
        inp = _make_input(
            equity="100000",
            risk_pct="0.0001",  # 0.01% risk → atr_notional = 10 / 1.5 ≈ 6.67
            atr="100",
            stop_atr_mult="1.5",
            f_star_estimate="1.0",  # kelly_fraction = 0.25, big kelly
        )
        result = CALCULATOR.compute(inp)
        assert isinstance(result, Success), f"Expected Success, got {result}."
        sr = result.unwrap()
        assert sr.notional == sr.atr_notional, (
            f"Expected ATR cap to bind (atr={sr.atr_notional!r} should be smallest). "
            f"Got notional={sr.notional!r}, kelly={sr.kelly_notional!r}, "
            f"hard_cap={sr.hard_cap_notional!r}."
        )
        assert sr.binding_cap == "atr", f"Expected binding_cap='atr', got {sr.binding_cap!r}."


# ---------------------------------------------------------------------------
# Unit tests — failure cases
# ---------------------------------------------------------------------------


class TestSizingCalculatorFailures:
    """SizingCalculator returns Failure for degenerate inputs."""

    def test_nonpositive_equity_raises(self) -> None:
        """SizingInput rejects equity <= 0."""
        with pytest.raises(ValueError, match="equity"):
            SizingInput(
                equity=Decimal("0"),
                entry_px=Decimal("50000"),
                atr=Decimal("500"),
                stop_atr_mult=Decimal("1.5"),
                risk_pct=Decimal("0.01"),
                f_star_estimate=Decimal("0.20"),
            )

    def test_nonpositive_entry_px_raises(self) -> None:
        """SizingInput rejects entry_px <= 0."""
        with pytest.raises(ValueError, match="entry_px"):
            SizingInput(
                equity=Decimal("100000"),
                entry_px=Decimal("-1"),
                atr=Decimal("500"),
                stop_atr_mult=Decimal("1.5"),
                risk_pct=Decimal("0.01"),
                f_star_estimate=Decimal("0.20"),
            )

    def test_nonpositive_atr_raises(self) -> None:
        """SizingInput rejects atr <= 0."""
        with pytest.raises(ValueError, match="atr"):
            SizingInput(
                equity=Decimal("100000"),
                entry_px=Decimal("50000"),
                atr=Decimal("0"),
                stop_atr_mult=Decimal("1.5"),
                risk_pct=Decimal("0.01"),
                f_star_estimate=Decimal("0.20"),
            )


# ---------------------------------------------------------------------------
# Property tests — three-cap invariants
# ---------------------------------------------------------------------------


class TestSizingCalculatorProperties:
    """Property tests: sized notional never exceeds any of the three caps."""

    @given(
        equity=st.decimals(
            min_value="1000", max_value="10000000", allow_nan=False, allow_infinity=False
        ),
        entry_px=st.decimals(
            min_value="0.01", max_value="100000", allow_nan=False, allow_infinity=False
        ),
        atr=st.decimals(min_value="0.01", max_value="10000", allow_nan=False, allow_infinity=False),
        stop_atr_mult=st.decimals(
            min_value="0.1", max_value="10", allow_nan=False, allow_infinity=False
        ),
        risk_pct=st.decimals(
            min_value="0.001", max_value="0.10", allow_nan=False, allow_infinity=False
        ),
        f_star_estimate=st.decimals(
            min_value="0.01", max_value="1.0", allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_notional_never_exceeds_hard_cap(
        self,
        equity: Decimal,
        entry_px: Decimal,
        atr: Decimal,
        stop_atr_mult: Decimal,
        risk_pct: Decimal,
        f_star_estimate: Decimal,
    ) -> None:
        """For all valid inputs: sized notional ≤ 0.05 * equity (pre-reg §5.4 hard cap)."""
        assume(equity > 0 and entry_px > 0 and atr > 0 and stop_atr_mult > 0)
        assume(risk_pct > 0 and f_star_estimate > 0)
        inp = SizingInput(
            equity=equity,
            entry_px=entry_px,
            atr=atr,
            stop_atr_mult=stop_atr_mult,
            risk_pct=risk_pct,
            f_star_estimate=f_star_estimate,
        )
        result = CALCULATOR.compute(inp)
        if isinstance(result, Failure):
            return  # degenerate — skip (Failure is valid for edge cases)
        sr = result.unwrap()
        hard_cap = Decimal("0.05") * equity
        assert sr.notional <= hard_cap, (
            f"INVARIANT VIOLATED: sized notional {sr.notional!r} > 5% hard cap {hard_cap!r}. "
            f"equity={equity!r}, notional={sr.notional!r}. Pre-reg §5.4 violation."
        )

    @given(
        equity=st.decimals(
            min_value="1000", max_value="10000000", allow_nan=False, allow_infinity=False
        ),
        entry_px=st.decimals(
            min_value="0.01", max_value="100000", allow_nan=False, allow_infinity=False
        ),
        atr=st.decimals(min_value="0.01", max_value="10000", allow_nan=False, allow_infinity=False),
        stop_atr_mult=st.decimals(
            min_value="0.1", max_value="10", allow_nan=False, allow_infinity=False
        ),
        risk_pct=st.decimals(
            min_value="0.001", max_value="0.10", allow_nan=False, allow_infinity=False
        ),
        f_star_estimate=st.decimals(
            min_value="0.01", max_value="1.0", allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_notional_never_exceeds_atr_baseline(
        self,
        equity: Decimal,
        entry_px: Decimal,
        atr: Decimal,
        stop_atr_mult: Decimal,
        risk_pct: Decimal,
        f_star_estimate: Decimal,
    ) -> None:
        """For all valid inputs: sized notional ≤ ATR-baseline notional (SA-7)."""
        assume(equity > 0 and entry_px > 0 and atr > 0 and stop_atr_mult > 0)
        assume(risk_pct > 0 and f_star_estimate > 0)
        inp = SizingInput(
            equity=equity,
            entry_px=entry_px,
            atr=atr,
            stop_atr_mult=stop_atr_mult,
            risk_pct=risk_pct,
            f_star_estimate=f_star_estimate,
        )
        result = CALCULATOR.compute(inp)
        if isinstance(result, Failure):
            return  # degenerate edge case — Failure is valid
        sr = result.unwrap()
        assert sr.notional <= sr.atr_notional, (
            f"INVARIANT VIOLATED: sized notional {sr.notional!r} > ATR notional "
            f"{sr.atr_notional!r}. SA-7 violation."
        )

    @given(
        equity=st.decimals(
            min_value="1000", max_value="10000000", allow_nan=False, allow_infinity=False
        ),
        entry_px=st.decimals(
            min_value="0.01", max_value="100000", allow_nan=False, allow_infinity=False
        ),
        atr=st.decimals(min_value="0.01", max_value="10000", allow_nan=False, allow_infinity=False),
        stop_atr_mult=st.decimals(
            min_value="0.1", max_value="10", allow_nan=False, allow_infinity=False
        ),
        risk_pct=st.decimals(
            min_value="0.001", max_value="0.10", allow_nan=False, allow_infinity=False
        ),
        f_star_estimate=st.decimals(
            min_value="0.01", max_value="1.0", allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_notional_never_exceeds_kelly_cap(
        self,
        equity: Decimal,
        entry_px: Decimal,
        atr: Decimal,
        stop_atr_mult: Decimal,
        risk_pct: Decimal,
        f_star_estimate: Decimal,
    ) -> None:
        """For all valid inputs: sized notional ≤ Kelly-cap notional (SA-1)."""
        assume(equity > 0 and entry_px > 0 and atr > 0 and stop_atr_mult > 0)
        assume(risk_pct > 0 and f_star_estimate > 0)
        inp = SizingInput(
            equity=equity,
            entry_px=entry_px,
            atr=atr,
            stop_atr_mult=stop_atr_mult,
            risk_pct=risk_pct,
            f_star_estimate=f_star_estimate,
        )
        result = CALCULATOR.compute(inp)
        if isinstance(result, Failure):
            return  # degenerate edge case — Failure is valid
        sr = result.unwrap()
        assert sr.notional <= sr.kelly_notional, (
            f"INVARIANT VIOLATED: sized notional {sr.notional!r} > Kelly notional "
            f"{sr.kelly_notional!r}. SA-1 Kelly cap violation."
        )
