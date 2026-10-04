"""Unit tests for scripts/backtest/run_calibration_full.py helpers.

Invariants (backtest-driver-mvp1-phase2 change.md D-4, R-1, R-6):

- The trial records the execution model and the pair next to the strategy
  parameters, so two runs with different depth are distinguishable trials.
- The trial's Sharpe is the per-day Sharpe of the daily equity curve, and
  ``None`` when it is undefined.
- ``--depth-frac`` accepts only a fraction in (0, 1].
- The report carries the kernel's strategy id and final portfolio.

The full run is smoke-tested against real data (see the change spec).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from hermes.application.backtest.replay import BacktestRun, ExecutionModel
from hermes.domain.statistics.performance import daily_returns
from hermes.domain.statistics.sharpe import sharpe_ratio
from tests.support.fill_kernel import seed

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "backtest"


def _import_driver() -> ModuleType:
    """Import the driver; it imports its sibling phase-1 module by name, as when run directly."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "run_calibration_full_driver", _SCRIPTS / "run_calibration_full.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_calibration_full_driver"] = module
    spec.loader.exec_module(module)
    return module


DRIVER = _import_driver()
_T0 = datetime(2024, 1, 1, tzinfo=UTC)


def test_trial_parameters_add_execution_model_and_pair() -> None:
    """Strategy parameters are kept and the execution model plus pair are added."""
    model = ExecutionModel(depth_frac=Decimal("0.1"))
    params = DRIVER._trial_parameters({"donchian_period": 20}, model, "btcusdt")
    assert params == {
        "donchian_period": 20,
        "pair": "BTCUSDT",
        "execution.fill_model": "orderbook_walk",
        "execution.entry_bps": Decimal("5"),
        "execution.stop_bps": Decimal("15"),
        "execution.depth_frac": Decimal("0.1"),
    }


def test_sharpe_is_the_daily_sharpe_of_the_equity_curve() -> None:
    """The trial Sharpe equals sharpe_ratio(daily_returns(curve)) on the same points."""
    values = [Decimal("5000"), Decimal("5010"), Decimal("5005"), Decimal("5030")]
    curve = [(_T0 + timedelta(days=i), v) for i, v in enumerate(values)]
    expected = sharpe_ratio(daily_returns([float(v) for v in values]))
    assert expected is not None
    assert DRIVER._sharpe(curve) == expected


def test_sharpe_is_none_for_a_flat_or_short_curve() -> None:
    """A curve with no dispersion or fewer than three points has no Sharpe."""
    flat = [(_T0 + timedelta(days=i), Decimal("5000")) for i in range(5)]
    short = [(_T0, Decimal("5000"))]
    assert (DRIVER._sharpe(flat), DRIVER._sharpe(short)) == (None, None)


@pytest.mark.parametrize("text", ["0", "-0.1", "1.5", "abc"])
def test_depth_frac_outside_unit_interval_is_refused(text: str) -> None:
    """Zero, negative, above-one or non-decimal depth fractions are usage errors."""
    with pytest.raises(argparse.ArgumentTypeError):
        DRIVER._depth_frac(text)


def test_depth_frac_inside_unit_interval_is_parsed_exactly() -> None:
    """A valid fraction parses to the exact Decimal."""
    assert DRIVER._depth_frac("0.25") == Decimal("0.25")


def test_headline_counts_declines_by_rule() -> None:
    """The trial headline lists declined signals per rule, sorted, plus the M-02 average."""
    run = BacktestRun(signals_seen=3, declined=Counter({"no-position": 1, "fill_model": 2}))
    headline = DRIVER._headline(run, {"m02_fill_price_delta_bps": {"avg_bps": "10"}})
    assert headline["declined"] == "fill_model=2,no-position=1"
    assert headline["m02_avg_bps"] == "10"
    assert headline["signals_seen"] == 3


def test_payload_reports_the_final_portfolio_and_strategy() -> None:
    """The report names the kernel's strategy and the final cash, equity and positions."""
    final = seed()
    payload = DRIVER._payload(
        pair="BTCUSDT",
        start=_T0,
        end=_T0 + timedelta(days=90),
        run=BacktestRun(),
        final=final,
        sharpe=None,
        model=ExecutionModel(),
        datasets=[],
        split="train",
        report_json={},
    )
    assert payload["strategy_id"] == "swing-donchian-4h"
    assert (payload["starting_cash"], payload["final_equity"], payload["open_positions"]) == (
        "5000",
        "5000",
        0,
    )
    assert payload["execution"] == {"entry_bps": "5", "stop_bps": "15", "depth_frac": "0.30"}
    assert payload["fill_model"] == "orderbook_walk"
