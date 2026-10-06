"""Unit tests for scripts/backtest/run_portfolio_backtest.py helpers (#822).

Invariants (822-portfolio-backtest change.md D-1, D-5, R-5):

- ``--pairs`` accepts only distinct symbols from the signed §3 universe.
- Bars of all pairs are delivered by close time, the daily bars first on ties.
- The trial records the pairs, the shared capital, the active risk rule and
  the execution model, so a portfolio run is a distinct trial from a per-pair one.
- The report lists every rule the run does not apply.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from hermes.application.backtest.replay import ExecutionModel
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "backtest"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
_T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _import_driver() -> ModuleType:
    """Import the driver; it imports its sibling modules by name, as when run directly."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "run_portfolio_backtest_driver", _SCRIPTS / "run_portfolio_backtest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_portfolio_backtest_driver"] = module
    spec.loader.exec_module(module)
    return module


DRIVER = _import_driver()


def _bar(pair: Pair, interval: KlineInterval, close_time: datetime) -> Bar:
    span = timedelta(days=1) if interval is KlineInterval.D1 else timedelta(hours=4)
    return Bar(
        pair=pair,
        interval=interval,
        open_time=close_time - span,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        close_time=close_time,
    )


def test_default_pairs_are_the_signed_universe() -> None:
    """With no --pairs the run trades BTC, ETH and SOL (§3)."""
    assert DRIVER._parse_pairs("BTCUSDT,ETHUSDT,SOLUSDT") == (_BTC, _ETH, _SOL)


@pytest.mark.parametrize("text", ["", "BTCUSDT,BTCUSDT", "BTCUSDT,ADAUSDT"])
def test_pairs_outside_the_universe_or_repeated_are_refused(text: str) -> None:
    """An empty list, a repeat or a symbol outside §3 is a usage error."""
    with pytest.raises(argparse.ArgumentTypeError):
        DRIVER._parse_pairs(text)


def test_bars_merge_by_close_time_daily_first() -> None:
    """At midnight every pair's 1D bar goes before any 4h bar closing then (#311)."""
    midnight = _T0 + timedelta(days=1)
    btc_4h = _bar(_BTC, KlineInterval.H4, midnight)
    eth_1d = _bar(_ETH, KlineInterval.D1, midnight)
    btc_1d = _bar(_BTC, KlineInterval.D1, midnight)
    earlier = _bar(_SOL, KlineInterval.H4, midnight - timedelta(hours=4))
    merged = DRIVER._merge([btc_4h, eth_1d, btc_1d, earlier])
    assert merged == [earlier, btc_1d, eth_1d, btc_4h]


def test_trial_parameters_name_the_portfolio() -> None:
    """The trial records the pairs, shared capital, RO-01 and the execution model."""
    params = DRIVER._trial_parameters(
        {"donchian_period": 20}, ExecutionModel(depth_frac=Decimal("0.1")), (_BTC, _ETH)
    )
    assert params == {
        "donchian_period": 20,
        "pairs": "BTC/USDT,ETH/USDT",
        "portfolio": "shared-capital",
        "risk.rules": "RO-01",
        "execution.fill_model": "orderbook_walk",
        "execution.entry_bps": Decimal("5"),
        "execution.stop_bps": Decimal("15"),
        "execution.depth_frac": Decimal("0.1"),
    }


def test_report_lists_every_rule_left_off() -> None:
    """Every SA-9 rule other than RO-01, and the kill evaluator, is listed as stubbed."""
    assert sorted(DRIVER.STUBBED) == [
        "RO-02 spread spike",
        "RO-03 max slippage",
        "RO-04 stale book",
        "RO-05/RO-06 depeg",
        "RO-07 macro ATR halt",
        "RO-08 portfolio drawdown",
        "kill evaluator",
    ]
