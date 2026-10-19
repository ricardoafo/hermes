#!/usr/bin/env python3
"""MVP1 backtest driver — Phase 2 (real fill path).

Runs the swing strategy over manifest-pinned Binance Vision bars and sends
every in-window signal through the fill kernel that ``hermes serve`` shares:

  FillKernel        — OrderRouter (entries) + OrderbookWalkFillModel
                      + PortfolioTracker + TradeExecuted v2
  replay            — one synthetic order book per signal, its touch at the
                      pre-registered price (§6.2): entries 5 bps over close,
                      chandelier exits 15 bps under the stop, other exits
                      5 bps under close; depth = bar volume x --depth-frac
  CalibrationHarness — M-01..M-07 over the fills, rejections and reference
                      prices

The strategy id and starting capital come from the enabled roster entry in
``config/strategies.yaml`` (``swing-donchian-4h``, 5000 USDT). Bars load
through the data manifest as an UNTRUSTED run, so the OOS window is refused.
Every run that evaluates the strategy appends a trial, with the Sharpe of its
daily equity curve, before it writes its report.

Usage:
    HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_calibration_full.py
    python scripts/backtest/run_calibration_full.py --pair ETHUSDT \\
        --start 2024-01-01 --end 2024-03-31 --output /tmp/p2.json
    python scripts/backtest/run_calibration_full.py --dry-run   # load bars, exit

Exit codes:
    0 - backtest completed, trial logged, report saved
    1 - fatal error (missing data, config, fee history, trial log)
    2 - usage error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from run_calibration import (
    DATA_MANIFEST_JSON,
    FEE_HISTORY_CSV,
    TRIAL_LOG_JSONL,
    _load_manifest,
    _load_verified_bars,
    _merge_by_close_time,
    _parse_iso_date,
    _parse_pair,
    _report_to_json,
    _strategy_parameters,
    _trial_record,
)

from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.backtest.replay import BacktestRun, ExecutionModel, replay
from hermes.application.calibration.harness import CalibrationHarness
from hermes.application.execution.kernel import FillKernel
from hermes.application.execution.prereg_book import ExitPricing
from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.sizing import SizingPolicy
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillTriggerEvaluator
from hermes.application.strategy_protocol.strategy_factory import strategy_kind
from hermes.application.strategy_protocol.trial_log import trial_split
from hermes.domain.errors import DataManifestFormatError, DomainError, TrialLogError
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.fees.fee_schedule_selector import (
    FeeHistoryOutOfRangeError,
    select_fee_schedule,
)
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.statistics.performance import daily_returns
from hermes.domain.statistics.sharpe import sharpe_ratio
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import load_config
from hermes.infrastructure.config.models import StrategyConfig
from hermes.infrastructure.logging import new_correlation_id
from hermes.infrastructure.project_root import load_pre_registration

_DEFAULT_PAIR = "BTCUSDT"
_DEFAULT_START = "2024-01-01"
_DEFAULT_END = "2024-03-31"
_RNG_SEED = 0


def _roster(config_dir: Path, name: str | None) -> StrategyConfig:
    """Return the roster entry named ``name``, or the single enabled entry serve also runs.

    A named entry runs whether or not it is enabled: serve's roster decides
    what trades, the backtest may replay any pre-registered variant.

    Raises:
        ValueError: No entry has ``name``, or ``name`` is ``None`` and not
            exactly one entry is enabled.
    """
    strategies = load_config(config_dir).strategies.strategies
    if name is not None:
        named = [s for s in strategies if s.name == name]
        if not named:
            raise ValueError(f"no strategy {name!r} in the roster {[s.name for s in strategies]}")
        return named[0]
    enabled = [s for s in strategies if s.enabled]
    if len(enabled) != 1:
        raise ValueError(f"expected exactly one enabled strategy, got {[s.name for s in enabled]}")
    return enabled[0]


def _select(
    config_dir: Path, name: str | None, depth_frac: Decimal
) -> tuple[StrategyConfig, ExecutionModel]:
    """Return the roster entry to run and the execution model it prices with.

    The model comes from the entry, as in serve, so a v2 run sells its exits
    at the bar close (pre-registration v2 §5.3, §6.2) and a v1 run as signed.

    Raises:
        ValueError: See :func:`_roster`.
    """
    roster = _roster(config_dir, name)
    return roster, roster.execution_model(depth_frac=depth_frac)


def _execution_fields(model: ExecutionModel) -> dict[str, Any]:
    """Return the execution settings a trial and a report record.

    The exit pricing is listed only when it is not v1's, so v1 records keep
    the shape they had when v1 was signed (v2-strategy-config D-4).
    """
    fields: dict[str, Any] = {
        "entry_bps": model.entry_bps,
        "stop_bps": model.stop_bps,
        "depth_frac": model.depth_frac,
    }
    if model.exit_pricing is not ExitPricing.STOP_ORDER:
        fields["exit_pricing"] = model.exit_pricing.value
    return fields


def _depth_frac(text: str) -> Decimal:
    """Parse ``--depth-frac``: a decimal in (0, 1]."""
    try:
        value = Decimal(text)
    except InvalidOperation as err:
        raise argparse.ArgumentTypeError(f"not a decimal: {text!r}") from err
    if not Decimal("0") < value <= Decimal("1"):
        raise argparse.ArgumentTypeError(f"--depth-frac must be in (0, 1], got {value}")
    return value


def _trial_parameters(strategy: dict[str, Any], model: ExecutionModel, pair: str) -> dict[str, Any]:
    """Return the strategy parameters plus the execution model and pair the run used."""
    return {
        **strategy,
        "pair": pair.upper(),
        "execution.fill_model": "orderbook_walk",
        **{f"execution.{key}": value for key, value in _execution_fields(model).items()},
    }


def _sharpe(daily_equity: Sequence[tuple[datetime, Decimal]]) -> float | None:
    """Return the per-day Sharpe ratio of the equity curve, or ``None`` when undefined."""
    return sharpe_ratio(daily_returns([float(equity) for _, equity in daily_equity]))


def _headline(run: BacktestRun, report_json: dict[str, Any]) -> dict[str, Any]:
    """Return the headline metrics a trial records."""
    m02 = report_json.get("m02_fill_price_delta_bps") or {}
    return {
        "signals_seen": run.signals_seen,
        "fills_count": len(run.fills),
        "rejections_count": len(run.rejections),
        "declined": ",".join(f"{rule}={n}" for rule, n in sorted(run.declined.items())),
        "m01_fill_count": report_json.get("m01_fill_count"),
        "m02_avg_bps": m02.get("avg_bps"),
        "m04_rejection_rate": report_json.get("m04_rejection_rate"),
        "m05_orphan_position_count": report_json.get("m05_orphan_position_count"),
    }


def _payload(
    *,
    pair: str,
    start: datetime,
    end: datetime,
    run: BacktestRun,
    final: PortfolioSnapshot,
    sharpe: float | None,
    model: ExecutionModel,
    datasets: list[dict[str, Any]],
    split: str,
    report_json: dict[str, Any],
) -> dict[str, Any]:
    """Return the JSON report body."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "pair": pair,
        "strategy_id": final.strategy_id.value,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "fill_model": "orderbook_walk",
        "execution": {key: str(value) for key, value in _execution_fields(model).items()},
        "signals_seen": run.signals_seen,
        "fills_count": len(run.fills),
        "rejections_count": len(run.rejections),
        "declined": dict(sorted(run.declined.items())),
        "starting_cash": str(final.starting_cash),
        "final_equity": str(final.equity),
        "realized_pnl": str(final.realized_pnl),
        "open_positions": len(final.positions),
        "daily_sharpe": sharpe,
        "daily_equity_points": len(run.daily_equity),
        "datasets": datasets,
        "split": split,
        "report": report_json,
    }


def _kernel(strategy_id: StrategyId, starting_cash: Money, sizing: SizingPolicy) -> FillKernel:
    """Compose the fill kernel exactly as serve will, with the backtest's empty rule set (D-5)."""
    return FillKernel(
        fill_model=OrderbookWalkFillModel(apply_slippage=False, rng=random.Random(_RNG_SEED)),  # noqa: S311 — deterministic replay, not crypto
        risk_engine=RiskEngine(rules=[]),
        kill_evaluator=KillTriggerEvaluator(pre_reg=load_pre_registration()),
        tracker=PortfolioTracker(PortfolioSnapshot.seed(strategy_id, starting_cash)),
        strategy_id=strategy_id,
        sizing=sizing,
    )


async def _run(args: argparse.Namespace) -> int:
    pair = _parse_pair(args.pair)
    start = _parse_iso_date(args.start)
    end = _parse_iso_date(args.end)
    try:
        roster, model = _select(Path(args.config_dir), args.strategy, args.depth_frac)
    except (ConfigError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    strategy_id = StrategyId(roster.name)
    print(
        f"Backtest window: {start.date()} -> {end.date()} (pair={args.pair}, {strategy_id.value})"
    )
    fee_rows = load_binance_fee_history(FEE_HISTORY_CSV)

    print(f"Loading historical bars, verified against {args.manifest}...")
    try:
        manifest = _load_manifest(Path(args.manifest))
        bars_4h, bars_1d, datasets = await _load_verified_bars(manifest, pair, start, end)
        split = trial_split(manifest, start, end)
    except (DataManifestFormatError, DomainError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"  bars_4h={len(bars_4h)} bars_1d={len(bars_1d)} datasets={len(datasets)}")
    if not bars_4h or not bars_1d:
        print("ERROR: no bars found in window — check HERMES_DATA_DIR + ingest", file=sys.stderr)
        return 1
    if args.dry_run:
        print("--dry-run set; skipping strategy execution.")
        return 0

    def fees_at(at: datetime, for_pair: Pair) -> FeeSchedule:
        return select_fee_schedule(fee_rows, at, for_pair)

    parameters = _strategy_parameters()
    kernel = _kernel(
        strategy_id,
        Money(amount=roster.capital_budget, currency=roster.quote),
        roster.sizing_policy(),
    )
    try:
        run = replay(
            _merge_by_close_time(bars_4h, bars_1d),
            strategy=strategy_kind(None).create(parameters),
            kernel=kernel,
            window_start=start,
            window_end=end,
            fees_at=fees_at,
            correlation_id=new_correlation_id,
            model=model,
        )
    except FeeHistoryOutOfRangeError as err:
        print(f"ERROR: fee history does not cover a signal: {err}", file=sys.stderr)
        return 1
    print(
        f"Replay: {run.signals_seen} signals, {len(run.fills)} fills, "
        f"{len(run.rejections)} rejections, declined={dict(run.declined)}"
    )

    report = CalibrationHarness(strategy_id=strategy_id.value).run(
        window_start=start,
        window_end=end,
        fills=run.fills,
        rejections=run.rejections,
        mid_prices=run.reference_prices,
    )
    report_json = _report_to_json(report)
    sharpe = _sharpe(run.daily_equity)

    # The trial is logged before the report exists, so no report exists without its trial.
    trial_log = JsonlTrialLog(Path(args.trial_log))
    try:
        record = _trial_record(
            strategy_id,
            _trial_parameters(parameters, model, args.pair),
            split,
            start,
            end,
            datasets,
            _headline(run, report_json),
        )
        trial_log.append(replace(record, sharpe=sharpe))
    except (TrialLogError, RuntimeError, OSError, subprocess.CalledProcessError) as err:
        print(f"ERROR: trial not logged, report not written: {err}", file=sys.stderr)
        return 1
    print(f"Trial logged: {args.trial_log} (split={split.value}, daily_sharpe={sharpe})")

    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        base = Path(os.environ.get("HERMES_DATA_DIR", "D:/hermes-data"))
        out_path = base / "backtests" / f"mvp1-p2-{args.pair.lower()}-{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(
        pair=args.pair,
        start=start,
        end=end,
        run=run,
        final=kernel.tracker.snapshot,
        sharpe=sharpe,
        model=model,
        datasets=datasets,
        split=split.value,
        report_json=report_json,
    )
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"Report saved: {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pair", default=_DEFAULT_PAIR, help="Trading pair symbol")
    parser.add_argument("--start", default=_DEFAULT_START, help="Window start (YYYY-MM-DD UTC)")
    parser.add_argument("--end", default=_DEFAULT_END, help="Window end (YYYY-MM-DD UTC)")
    parser.add_argument("--output", default=None, help="Output JSON path (default: auto)")
    parser.add_argument("--dry-run", action="store_true", help="Load bars, skip strategy + save")
    parser.add_argument(
        "--trial-log",
        default=str(TRIAL_LOG_JSONL),
        help="Append-only trial log every run writes to",
    )
    parser.add_argument(
        "--manifest", default=str(DATA_MANIFEST_JSON), help="Data manifest the bars must match"
    )
    parser.add_argument(
        "--depth-frac",
        type=_depth_frac,
        default=ExecutionModel().depth_frac,
        help="Fraction of each bar's volume resting on each synthetic book side",
    )
    parser.add_argument("--config-dir", default="config", help="Directory holding strategies.yaml")
    parser.add_argument(
        "--strategy",
        default=None,
        help="Roster entry to run, e.g. swing-mvp1-v2 (default: the single enabled entry)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
