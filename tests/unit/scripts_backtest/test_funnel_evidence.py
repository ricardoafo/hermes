"""Unit tests for scripts/backtest/funnel_evidence.py (#997).

Invariants (v2-funnel-evidence change.md D-1 to D-7):

- The replay grid is the base run, the six pre-registration v2 §8.5
  neighbours (one parameter moved at a time) and one 2x cost-stress replay.
- A neighbour that names an unknown parameter or repeats the base refuses.
- The stress replay doubles every execution bps and every fee tier; the other
  replays keep the loaded costs.
- Only funnel replays add trial fields; the base trial line is unchanged.
- The evidence dates each point by the day its daily bar closes, holds the
  base run and its neighbours in ``trials`` and the stress run in
  ``cost_stress``, and refuses replays on different or missing days.
"""

import re
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.backtest.replay import ExecutionModel
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.statistics.performance import daily_returns
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.support.paths import REPO_ROOT

_SCRIPTS = REPO_ROOT / "scripts" / "backtest"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import funnel_evidence as fe  # noqa: E402 — the scripts directory is not a package

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_BASE = {
    "donchian_period": 20,
    "adx_threshold": Decimal("25"),
    "chandelier_atr_mult": Decimal("3.0"),
    "ema_period": 200,
}
_MIDNIGHT = datetime(2021, 1, 2, tzinfo=UTC)
_LAST_MS = datetime(2021, 1, 1, 23, 59, 59, 999000, tzinfo=UTC)


def _curve(values: list[str], first_close: datetime = _MIDNIGHT) -> list[tuple[datetime, Decimal]]:
    return [(first_close + timedelta(days=i), Decimal(v)) for i, v in enumerate(values)]


def _curves(values: list[str]) -> dict[str, list[tuple[datetime, Decimal]]]:
    return {replay.trial_id: _curve(values) for replay in fe.FUNNEL_REPLAYS}


def test_grid_is_the_base_six_neighbours_and_the_cost_stress() -> None:
    """The replays are §8.5's, in order: base, Donchian, ADX, chandelier multiple, stress."""
    assert [r.trial_id for r in fe.FUNNEL_REPLAYS] == [
        "base",
        "donchian_period=15",
        "donchian_period=25",
        "adx_threshold=20",
        "adx_threshold=30",
        "chandelier_atr_mult=2.5",
        "chandelier_atr_mult=3.5",
        "cost_stress=2",
    ]


def test_neighbours_are_the_neighbour_ids() -> None:
    """``NEIGHBOUR_TRIAL_IDS`` is every replay that moves a parameter and nothing else."""
    assert tuple(r.trial_id for r in fe.FUNNEL_REPLAYS[1:7]) == fe.NEIGHBOUR_TRIAL_IDS


@pytest.mark.parametrize("replay", fe.FUNNEL_REPLAYS[1:7], ids=lambda r: r.trial_id)
def test_a_neighbour_moves_exactly_one_parameter(replay: fe.FunnelReplay) -> None:
    """A neighbour's parameters differ from the base in its one named parameter only."""
    moved = replay.parameters(_BASE)
    changed = {k for k in _BASE if moved[k] != _BASE[k]}
    assert changed == set(replay.overrides)
    assert replay.cost_multiplier == 1


def test_neighbour_values_are_the_pre_registered_ones() -> None:
    """The overrides carry §8.5's values with the strategy's parameter types."""
    assert [r.overrides for r in fe.FUNNEL_REPLAYS[1:7]] == [
        {"donchian_period": 15},
        {"donchian_period": 25},
        {"adx_threshold": Decimal("20")},
        {"adx_threshold": Decimal("30")},
        {"chandelier_atr_mult": Decimal("2.5")},
        {"chandelier_atr_mult": Decimal("3.5")},
    ]


def test_base_and_stress_keep_the_base_parameters() -> None:
    """The base and the cost stress replay run the loaded parameters unchanged."""
    assert fe.FUNNEL_REPLAYS[0].parameters(_BASE) == _BASE
    assert fe.FUNNEL_REPLAYS[-1].parameters(_BASE) == _BASE


def test_an_override_of_an_unknown_parameter_refuses() -> None:
    """A neighbour naming a parameter the strategy does not take is an error, not a no-op."""
    with pytest.raises(ValueError, match="donchian_period"):
        fe.FUNNEL_REPLAYS[1].parameters({"adx_threshold": Decimal("25")})


def test_a_neighbour_equal_to_the_base_refuses() -> None:
    """A neighbour that repeats the base value would be the base run twice."""
    with pytest.raises(ValueError, match="equals the base"):
        fe.FUNNEL_REPLAYS[1].parameters({**_BASE, "donchian_period": 15})


def test_stress_doubles_every_execution_bps_and_keeps_depth() -> None:
    """§6.2: the stress model's entry and stop bps are twice the loaded ones; depth is unchanged."""
    model = ExecutionModel(
        entry_bps=Decimal("5"), stop_bps=Decimal("15"), depth_frac=Decimal("0.2")
    )
    stressed = fe.FUNNEL_REPLAYS[-1].model(model)
    assert stressed == ExecutionModel(
        entry_bps=Decimal("10"), stop_bps=Decimal("30"), depth_frac=Decimal("0.2")
    )


@pytest.mark.parametrize("replay", fe.FUNNEL_REPLAYS[:7], ids=lambda r: r.trial_id)
def test_other_replays_keep_the_loaded_model(replay: fe.FunnelReplay) -> None:
    """Only the stress replay changes execution costs."""
    model = ExecutionModel(depth_frac=Decimal("0.2"))
    assert replay.model(model) is model


def test_stress_doubles_every_fee_tier() -> None:
    """§6.2: every tier's maker and taker bps are twice the schedule's."""
    schedule = FeeSchedule(
        pair=_BTC,
        tiers=(
            FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=10, taker_bps=10),
            FeeTier(volume_threshold_usd=Decimal("1000000"), maker_bps=9, taker_bps=10),
        ),
    )
    fees = fe.FUNNEL_REPLAYS[-1].fees(lambda _at, _pair: schedule)
    stressed = fees(_MIDNIGHT, _BTC)
    assert [(t.volume_threshold_usd, t.maker_bps, t.taker_bps) for t in stressed.tiers] == [
        (Decimal("0"), 20, 20),
        (Decimal("1000000"), 18, 20),
    ]


def test_base_fees_are_the_loaded_fees() -> None:
    """The base replay prices fees from the loaded history itself."""

    def fees_at(_at: datetime, _pair: Pair) -> FeeSchedule:
        raise AssertionError("not called")

    assert fe.FUNNEL_REPLAYS[0].fees(fees_at) is fees_at


def test_base_trial_line_has_no_funnel_fields() -> None:
    """The base trial is the same trial as a run without the flag."""
    assert fe.FUNNEL_REPLAYS[0].trial_fields() == {}


def test_neighbour_trial_line_names_its_replay() -> None:
    """A neighbour's trial line carries its trial id, so a missing one shows in the log."""
    assert fe.FUNNEL_REPLAYS[1].trial_fields() == {"funnel.replay": "donchian_period=15"}


def test_stress_trial_line_names_its_replay_and_multiplier() -> None:
    """The stress line records the fee multiplier, which the execution fields do not show."""
    assert fe.FUNNEL_REPLAYS[-1].trial_fields() == {
        "funnel.replay": "cost_stress=2",
        "execution.cost_multiplier": 2,
    }


def test_evidence_holds_the_base_run_and_its_sections() -> None:
    """The evidence carries the base curve, trade count, stress curve and no running strategy."""
    curves = _curves(["5000", "5050", "5000"])
    curves["cost_stress=2"] = _curve(["5000", "5040", "4980"])
    evidence = fe.funnel_evidence("swing-x", curves, trade_count=7)
    assert evidence["strategy_id"] == "swing-x"
    assert evidence["daily_equity"] == [5000.0, 5050.0, 5000.0]
    assert evidence["trade_count"] == 7
    assert evidence["cost_stress"] == {"multiplier": 2.0, "daily_equity": [5000.0, 5040.0, 4980.0]}
    assert evidence["neighbour_trial_ids"] == list(fe.NEIGHBOUR_TRIAL_IDS)
    assert evidence["running_strategies"] == {}


def test_trials_are_the_base_and_neighbours_not_the_stress() -> None:
    """§8.5: the CSCV matrix is the base run plus its six neighbours."""
    evidence = fe.funnel_evidence("swing-x", _curves(["5000", "5050", "5000"]), trade_count=0)
    assert list(evidence["trials"]) == ["base", *fe.NEIGHBOUR_TRIAL_IDS]


def test_trial_series_are_the_daily_returns_of_each_curve() -> None:
    """Each trial column is the simple daily return series of its own replay."""
    curves = _curves(["5000", "5050", "5000"])
    curves["adx_threshold=30"] = _curve(["5000", "4900", "4949"])
    trials = fe.funnel_evidence("swing-x", curves, trade_count=0)["trials"]
    assert trials["adx_threshold=30"] == daily_returns([5000.0, 4900.0, 4949.0])
    assert trials["base"] == daily_returns([5000.0, 5050.0, 5000.0])


@pytest.mark.parametrize(
    ("first_close", "day"),
    [(_MIDNIGHT, date(2021, 1, 1)), (_LAST_MS, date(2021, 1, 1))],
    ids=["midnight-close", "last-millisecond-close"],
)
def test_start_date_is_the_day_the_first_daily_bar_closes(first_close: datetime, day: date) -> None:
    """A 1D bar closing at 23:59:59.999 or at the next midnight dates the day it ends."""
    curves = {r.trial_id: _curve(["5000", "5001"], first_close) for r in fe.FUNNEL_REPLAYS}
    assert fe.funnel_evidence("swing-x", curves, trade_count=0)["start_date"] == day.isoformat()


def test_replays_on_different_days_refuse() -> None:
    """Every series must cover the same days, or PBO compares different stretches."""
    curves = _curves(["5000", "5050", "5000"])
    curves["cost_stress=2"] = _curve(["5000", "5050", "5000"], _MIDNIGHT + timedelta(days=1))
    with pytest.raises(ValueError, match="cost_stress=2"):
        fe.funnel_evidence("swing-x", curves, trade_count=0)


def test_a_missing_day_refuses() -> None:
    """A gap in the curve would misdate every later point."""
    gapped = [(_MIDNIGHT, Decimal("5000")), (_MIDNIGHT + timedelta(days=2), Decimal("5010"))]
    curves = {r.trial_id: gapped for r in fe.FUNNEL_REPLAYS}
    with pytest.raises(ValueError, match="consecutive"):
        fe.funnel_evidence("swing-x", curves, trade_count=0)


def test_a_missing_replay_refuses() -> None:
    """Evidence without every replay of the grid is never written."""
    curves = _curves(["5000", "5050"])
    del curves["chandelier_atr_mult=3.5"]
    with pytest.raises(ValueError, match=re.escape("chandelier_atr_mult=3.5")):
        fe.funnel_evidence("swing-x", curves, trade_count=0)


@given(
    st.lists(
        st.decimals(min_value=Decimal("1"), max_value=Decimal("100000"), places=2),
        min_size=2,
        max_size=40,
    )
)
def test_every_trial_has_one_return_per_day_after_the_first(values: list[Decimal]) -> None:
    """For any curve, every trial holds len(daily_equity) - 1 returns that compound to its end."""
    curve = [(_MIDNIGHT + timedelta(days=i), v) for i, v in enumerate(values)]
    evidence = fe.funnel_evidence(
        "swing-x", {r.trial_id: curve for r in fe.FUNNEL_REPLAYS}, trade_count=0
    )
    growth = 1.0
    for r in evidence["trials"]["base"]:
        growth *= 1.0 + r
    assert len(evidence["trials"]["base"]) == len(evidence["daily_equity"]) - 1
    assert growth == pytest.approx(float(values[-1]) / float(values[0]), rel=1e-9)
