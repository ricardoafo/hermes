"""Tests for SizingCalculator against pre-registration-v1.md §2, §5.4 and §5.5.

The worked examples pin the signed formula at the pre-registered 5,000 USDT
capital: risk 1% of capital ÷ stop distance * entry, a 5% per-position cap and
a 15% total exposure cap. The properties hold the caps for any configured
capital and percentages.

Reference: openspec/archive/sizing-per-prereg/change.md R-1..R-3.
"""

from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from returns.result import Failure, Success

from hermes.application.order_router.sizing import (
    SizingCalculator,
    SizingInput,
    SizingPolicy,
    SizingResult,
)
from hermes.domain.errors import InvalidLot

# Pre-registration §2 capital with the §5.4/§5.5 shares, as in config/strategies.yaml.
_PREREG = SizingPolicy(
    capital=Decimal("5000"),
    risk_pct=Decimal("0.01"),
    max_position_pct=Decimal("0.05"),
    max_total_exposure_pct=Decimal("0.15"),
)


def _size(
    entry: str,
    stop: str,
    *,
    open_notional: str = "0",
    policy: SizingPolicy = _PREREG,
) -> SizingResult:
    """Size an entry that must succeed and return its result."""
    result = SizingCalculator(policy).compute(
        SizingInput(
            entry_px=Decimal(entry),
            stop_px=Decimal(stop),
            open_notional=Decimal(open_notional),
        )
    )
    assert isinstance(result, Success), f"expected a sized position, got {result!r}"
    sized: SizingResult = result.unwrap()
    return sized


def _failure_reason(entry: str, stop: str, *, open_notional: str = "0") -> str:
    """Size an entry that must fail and return the InvalidLot reason."""
    result = SizingCalculator(_PREREG).compute(
        SizingInput(
            entry_px=Decimal(entry),
            stop_px=Decimal(stop),
            open_notional=Decimal(open_notional),
        )
    )
    assert isinstance(result, Failure), f"expected sizing to fail, got {result!r}"
    error = result.failure()
    assert isinstance(error, InvalidLot)
    return error.reason


# ---------------------------------------------------------------------------
# §5.4 / §5.5 worked examples at 5,000 USDT
# ---------------------------------------------------------------------------


class TestPreRegistrationWorkedExamples:
    """The pre-registered numbers come out exactly at 5,000 USDT."""

    def test_wide_stop_sizes_by_risk_alone(self) -> None:
        """Entry 100, stop 50: 50 USDT risk ÷ 50 * 100 = 100 USDT, under the 250 cap."""
        assert _size("100", "50").notional == Decimal("100")

    def test_wide_stop_buys_notional_over_entry(self) -> None:
        """Entry 100, stop 50: 100 USDT notional buys exactly 1 unit."""
        assert _size("100", "50").qty == Decimal("1")

    def test_wide_stop_is_bound_by_risk(self) -> None:
        """When the risk formula is below the cap, the result names the risk formula."""
        assert _size("100", "50").binding_cap == "risk"

    def test_risk_formula_uses_the_stop_distance(self) -> None:
        """Entry 100, stop 95: the §5.4 formula gives 50 ÷ 5 * 100 = 1,000 USDT before the cap."""
        assert _size("100", "95").risk_notional == Decimal("1000")

    # SEV-1: no rubric row matches; pre-reg §5.4 per-position cap stops over-exposure
    def test_tight_stop_is_capped_at_five_percent_of_capital(self) -> None:
        """Entry 100, stop 95: the 1,000 USDT formula result is cut to the 250 USDT hard cap."""
        assert _size("100", "95").notional == Decimal("250")

    def test_tight_stop_is_bound_by_the_position_cap(self) -> None:
        """When the hard cap cuts the size, the result names the position cap."""
        assert _size("100", "95").binding_cap == "position_cap"

    def test_third_position_fills_the_exposure_cap_exactly(self) -> None:
        """Two open 250 USDT positions plus a third 250 reach 750 USDT, which is allowed."""
        assert _size("100", "95", open_notional="500").notional == Decimal("250")

    # SEV-1: no rubric row matches; pre-reg §5.5 total exposure cap stops over-exposure
    def test_fourth_position_is_blocked_at_the_exposure_cap(self) -> None:
        """With 750 USDT already open, any new entry would pass 15% of capital: blocked."""
        assert "total exposure cap" in _failure_reason("100", "95", open_notional="750")

    # SEV-1: no rubric row matches; pre-reg §5.5 total exposure cap stops over-exposure
    def test_entry_that_would_pass_the_exposure_cap_is_blocked_not_shrunk(self) -> None:
        """600 open + 250 new = 850 > 750: the entry is refused, not resized to the 150 headroom."""
        assert "total exposure cap" in _failure_reason("100", "95", open_notional="600")

    def test_small_entry_fits_under_the_remaining_headroom(self) -> None:
        """650 open + 100 new = 750: a risk-sized entry that fits the headroom is allowed."""
        assert _size("100", "50", open_notional="650").notional == Decimal("100")


# ---------------------------------------------------------------------------
# Configured capital drives every limit
# ---------------------------------------------------------------------------


class TestCapitalScalesTheLimits:
    """Doubling the configured capital doubles risk, position cap and exposure cap."""

    _DOUBLE = SizingPolicy(
        capital=Decimal("10000"),
        risk_pct=Decimal("0.01"),
        max_position_pct=Decimal("0.05"),
        max_total_exposure_pct=Decimal("0.15"),
    )

    def test_risk_sized_notional_follows_capital(self) -> None:
        """At 10,000 USDT, entry 100 stop 50 sizes 100 ÷ 50 * 100 = 200 USDT."""
        assert _size("100", "50", policy=self._DOUBLE).notional == Decimal("200")

    def test_position_cap_follows_capital(self) -> None:
        """At 10,000 USDT, the tight-stop entry is capped at 500 USDT."""
        assert _size("100", "95", policy=self._DOUBLE).notional == Decimal("500")

    def test_exposure_cap_follows_capital(self) -> None:
        """At 10,000 USDT, 1,000 open + 500 new = 1,500 is allowed; at 5,000 it is blocked."""
        sized = _size("100", "95", open_notional="1000", policy=self._DOUBLE)
        assert sized.notional == Decimal("500")

    def test_policy_limits_are_shares_of_capital(self) -> None:
        """The three limits are risk_pct, max_position_pct and max_total_exposure_pct of capital."""
        assert (
            self._DOUBLE.risk_budget,
            self._DOUBLE.position_cap,
            self._DOUBLE.exposure_cap,
        ) == (Decimal("100"), Decimal("500"), Decimal("1500"))


# ---------------------------------------------------------------------------
# Rejected inputs
# ---------------------------------------------------------------------------


class TestRejectedInputs:
    """Inputs that cannot be sized fail loudly instead of producing a position."""

    @pytest.mark.parametrize("stop", ["100", "101"])
    def test_stop_not_below_entry_fails(self, stop: str) -> None:
        """A long entry with the stop at or above the entry has no defined risk: no position."""
        assert "not below entry" in _failure_reason("100", stop)

    @pytest.mark.parametrize("field", ["entry_px", "stop_px"])
    def test_non_positive_price_is_rejected(self, field: str) -> None:
        """A zero price cannot come from a real bar; SizingInput refuses it."""
        prices = {"entry_px": Decimal("100"), "stop_px": Decimal("95"), field: Decimal("0")}
        with pytest.raises(ValueError, match=field):
            SizingInput(open_notional=Decimal("0"), **prices)

    def test_negative_open_notional_is_rejected(self) -> None:
        """A negative open notional would silently widen the exposure headroom."""
        with pytest.raises(ValueError, match="open_notional"):
            SizingInput(entry_px=Decimal("100"), stop_px=Decimal("95"), open_notional=Decimal("-1"))

    def test_non_positive_capital_is_rejected(self) -> None:
        """A policy with no capital has no limits to scale from."""
        with pytest.raises(ValueError, match="capital"):
            SizingPolicy(
                capital=Decimal("0"),
                risk_pct=Decimal("0.01"),
                max_position_pct=Decimal("0.05"),
                max_total_exposure_pct=Decimal("0.15"),
            )

    @pytest.mark.parametrize("share", ["0", "1.01"])
    def test_share_outside_zero_one_is_rejected(self, share: str) -> None:
        """A share of capital must lie in (0, 1]; zero or above 100% is a config error."""
        with pytest.raises(ValueError, match="max_position_pct"):
            SizingPolicy(
                capital=Decimal("5000"),
                risk_pct=Decimal("0.01"),
                max_position_pct=Decimal(share),
                max_total_exposure_pct=Decimal("0.15"),
            )


# ---------------------------------------------------------------------------
# Properties — any capital, any shares, any stop
# ---------------------------------------------------------------------------

_SHARE = st.decimals(min_value="0.0001", max_value="1", places=4)


@st.composite
def _sizing_case(draw: st.DrawFn) -> tuple[SizingPolicy, SizingInput]:
    """Draw a valid policy and an entry with its stop strictly below the entry."""
    policy = SizingPolicy(
        capital=draw(st.decimals(min_value="1", max_value="10000000", places=2)),
        risk_pct=draw(_SHARE),
        max_position_pct=draw(_SHARE),
        max_total_exposure_pct=draw(_SHARE),
    )
    entry = draw(st.decimals(min_value="0.0001", max_value="1000000", places=4))
    stop_fraction = draw(st.decimals(min_value="0.0001", max_value="0.9999", places=4))
    open_share = draw(st.decimals(min_value="0", max_value="2", places=4))
    return policy, SizingInput(
        entry_px=entry,
        stop_px=entry * stop_fraction,
        open_notional=policy.exposure_cap * open_share,
    )


class TestSizingProperties:
    """The caps hold for every configured capital and share, not only the pinned numbers."""

    # SEV-1: no rubric row matches; pre-reg §5.4 per-position cap stops over-exposure
    @given(case=_sizing_case())
    @settings(max_examples=300)
    def test_notional_never_exceeds_the_position_cap(
        self, case: tuple[SizingPolicy, SizingInput]
    ) -> None:
        """No sized position is larger than max_position_pct * capital."""
        policy, inp = case
        result = SizingCalculator(policy).compute(inp)
        if isinstance(result, Success):
            assert result.unwrap().notional <= policy.position_cap

    # SEV-1: no rubric row matches; pre-reg §5.4 risk budget bounds the loss at the stop
    @given(case=_sizing_case())
    @settings(max_examples=300)
    def test_loss_at_the_stop_never_exceeds_the_risk_budget(
        self, case: tuple[SizingPolicy, SizingInput]
    ) -> None:
        """The loss qty * (entry - stop), computed exactly, never exceeds risk_pct * capital."""
        policy, inp = case
        result = SizingCalculator(policy).compute(inp)
        if isinstance(result, Success):
            distance = Fraction(inp.entry_px) - Fraction(inp.stop_px)
            assert Fraction(result.unwrap().qty) * distance <= Fraction(policy.risk_budget)

    # SEV-1: no rubric row matches; pre-reg §5.5 total exposure cap stops over-exposure
    @given(case=_sizing_case())
    @settings(max_examples=300)
    def test_open_plus_new_notional_never_exceeds_the_exposure_cap(
        self, case: tuple[SizingPolicy, SizingInput]
    ) -> None:
        """A sized entry always leaves open notional within max_total_exposure_pct * capital."""
        policy, inp = case
        result = SizingCalculator(policy).compute(inp)
        if isinstance(result, Success):
            assert inp.open_notional + result.unwrap().notional <= policy.exposure_cap

    @given(case=_sizing_case())
    @settings(max_examples=300)
    def test_qty_never_buys_more_than_the_notional(
        self, case: tuple[SizingPolicy, SizingInput]
    ) -> None:
        """Rounding never goes up: qty * entry is at most the sized notional."""
        policy, inp = case
        result = SizingCalculator(policy).compute(inp)
        if isinstance(result, Success):
            sized = result.unwrap()
            assert Fraction(sized.qty) * Fraction(inp.entry_px) <= Fraction(sized.notional)
