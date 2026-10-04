"""Invariants of the console view objects.

- Net is always gross minus fees minus tax; a pending tax counts as zero.
- A DOWN heartbeat carries the time it went down, and only a DOWN one does.
- The observation drift summary reads the size of the drift, not its sign.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.views import (
    Heartbeat,
    HeartbeatState,
    KillCriterion,
    KillState,
    KillTrigger,
    PositionDetail,
    PositionRow,
    TaxEstimate,
    TaxPending,
    WaterfallColumn,
    tax_amount,
)
from tests.unit.application.console._observation import not_started, stats, window

_T0 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_money = st.decimals(min_value=-100_000, max_value=100_000, places=2, allow_nan=False)
_cost = st.decimals(min_value=0, max_value=10_000, places=2, allow_nan=False)


def _row() -> PositionRow:
    zero = Decimal(0)
    return PositionRow(
        coin="ETH",
        timeframe="4h",
        side="long",
        size=Decimal("0.035"),
        opened_at=_T0,
        entry_price=Decimal(2618),
        current_price=Decimal(2672),
        change_pct=zero,
        net=zero,
        stop_price=Decimal(2580),
        high_since_entry=Decimal(2690),
        stop_distance_atr=zero,
        stop_distance_pct=zero,
    )


def _detail(
    gross: Decimal, paid: Decimal, to_close: Decimal, tax: TaxEstimate | TaxPending
) -> PositionDetail:
    return PositionDetail(
        row=_row(),
        strategy="s",
        entry_values=(),
        stop_moves=0,
        gross=gross,
        fees_paid=paid,
        fees_to_close=to_close,
        tax=tax,
        price_path=(),
        timeline=(),
    )


@given(gross=_money, fees=_cost, tax=_money)
def test_waterfall_net_is_gross_minus_fees_minus_tax(
    gross: Decimal, fees: Decimal, tax: Decimal
) -> None:
    """Waterfall net equals gross - fees - estimated tax for any figures."""
    column = WaterfallColumn(gross=gross, fees=fees, tax=TaxEstimate(tax))
    assert column.net == gross - fees - tax


@given(gross=_money, fees=_cost)
def test_waterfall_net_is_before_tax_while_tax_is_pending(gross: Decimal, fees: Decimal) -> None:
    """A pending tax leaves waterfall net at gross - fees."""
    column = WaterfallColumn(gross=gross, fees=fees, tax=TaxPending())
    assert column.net == gross - fees


@given(gross=_money, paid=_cost, to_close=_cost, tax=_money)
def test_position_net_subtracts_both_fees_and_tax(
    gross: Decimal, paid: Decimal, to_close: Decimal, tax: Decimal
) -> None:
    """Position net equals gross - fees paid - fees to close - tax."""
    detail = _detail(gross, paid, to_close, TaxEstimate(tax))
    assert detail.net == gross - paid - to_close - tax


def test_tax_credit_raises_net() -> None:
    """A negative tax estimate (a credit) increases net instead of reducing it."""
    column = WaterfallColumn(
        gross=Decimal("-42.60"), fees=Decimal("6.20"), tax=TaxEstimate(Decimal("-9.27"))
    )
    assert column.net == Decimal("-39.53")


def test_pending_tax_reads_not_available_yet() -> None:
    """The pending placeholder carries the agreed operator-facing wording."""
    assert TaxPending().label == "tax estimate: not available yet"


def test_pending_tax_amount_is_zero() -> None:
    """A pending tax reduces nothing."""
    assert tax_amount(TaxPending()) == Decimal(0)


def test_down_heartbeat_without_start_time_is_rejected() -> None:
    """A DOWN heartbeat must say since when hermes is down."""
    with pytest.raises(ValueError, match="down_since"):
        Heartbeat(
            state=HeartbeatState.DOWN,
            as_of=_T0,
            last_check_at=_T0,
            next_check_at=_T0,
        )


def test_live_heartbeat_with_down_time_is_rejected() -> None:
    """An OK heartbeat cannot carry a down time."""
    with pytest.raises(ValueError, match="down_since"):
        Heartbeat(
            state=HeartbeatState.OK,
            as_of=_T0,
            last_check_at=_T0,
            next_check_at=_T0,
            down_since=_T0,
        )


def test_down_heartbeat_marks_numbers_stale_from_down_time() -> None:
    """Money is stale from the moment hermes went down."""
    heartbeat = Heartbeat(
        state=HeartbeatState.DOWN,
        as_of=_T0,
        last_check_at=_T0,
        next_check_at=_T0,
        down_since=datetime(2026, 10, 3, 11, 4, tzinfo=UTC),
    )
    assert heartbeat.stale_since == datetime(2026, 10, 3, 11, 4, tzinfo=UTC)


def test_observation_drift_summary_reads_absolute_drift() -> None:
    """Drift of -40 bps is 40 bps off the model, as the §7 gate reads it."""
    summary = stats(drift=("-40", "-40", "10")).drift
    assert summary is not None
    assert summary.p50 == Decimal(40)


def test_observation_verdict_follows_to_date_samples() -> None:
    """A stats object with drift at the gate reports a failing verdict."""
    assert stats(drift=("31", "30", "35")).drift_verdict.value == "fail"


def test_only_the_sharpe_criterion_reveals_pnl() -> None:
    """Drift days and shock fires are execution facts; Sharpe days are P&L."""
    assert [c for c in KillCriterion if c.reveals_pnl] == [KillCriterion.SHADOW_SHARPE]


def test_kill_trigger_count_cannot_be_negative() -> None:
    """A negative progress count is a read-model bug."""
    with pytest.raises(ValueError, match="negative"):
        KillTrigger(KillCriterion.SHOCK_OVERLAY, KillState.ARMED, -1)


def test_window_before_day_one_is_not_started() -> None:
    """A not-started shadow test means the window has no day 1."""
    assert (window().started, not_started().started) == (True, False)
