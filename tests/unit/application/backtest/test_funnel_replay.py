"""Tests for the funnel replay helpers shared by pre-registration v2 and batch candidates."""

from decimal import Decimal

from hermes.application.backtest.funnel_replay import (
    BASE_TRIAL_ID,
    COST_STRESS,
    COST_STRESS_MULTIPLIER,
    FunnelReplay,
    neighbour,
)


def test_a_neighbour_moves_one_parameter_and_is_named_by_it() -> None:
    """The trial id ``name=value`` is how evidence and trial lines name a neighbour."""
    replay = neighbour("atr_mult", Decimal("2.5"))

    assert (replay.trial_id, dict(replay.overrides), replay.cost_multiplier) == (
        "atr_mult=2.5",
        {"atr_mult": Decimal("2.5")},
        1,
    )


def test_the_cost_stress_doubles_costs_and_keeps_the_base_parameters() -> None:
    """ADR-021 §5: still profitable at 2x fees and slippage."""
    assert (COST_STRESS.trial_id, dict(COST_STRESS.overrides), COST_STRESS.cost_multiplier) == (
        "cost_stress=2",
        {},
        COST_STRESS_MULTIPLIER,
    )


def test_the_base_trial_line_adds_nothing() -> None:
    """The base run's trial line carries only the strategy's own parameters."""
    assert FunnelReplay(trial_id=BASE_TRIAL_ID).trial_fields() == {}
