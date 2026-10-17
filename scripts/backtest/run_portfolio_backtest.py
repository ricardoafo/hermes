#!/usr/bin/env python3
"""MVP1 portfolio backtest: BTC, ETH and SOL on one capital (issue #822).

Pre-registration v1 is a portfolio experiment: the three pairs of §3 share
the 5,000 USDT of §2, with at most 3 open positions, the 15% total exposure
cap and the 0.80 correlation lock of §5.5. This driver replays all three
pairs' manifest-pinned bars, ordered by close time, through one
``FillKernel`` and one portfolio, so every §5.4/§5.5 limit binds across pairs
and the equity curve is the experiment's.

- Sizing, position limit and exposure cap: the kernel's router, as in serve.
- Correlation lock (RO-01, as signed): the 30-day correlation of daily
  returns, from the daily bars already replayed.
- Risk rules that need data a bar backtest does not have stay off; the
  report lists them under ``stubbed``.

Every run that evaluates the strategy appends one trial (pre-registration
§8.1; a window that touches Validation counts as a Validation touch), then
writes its report and the ``hermes certify --performance`` file. Bars load
through the data manifest as an UNTRUSTED run, so the OOS window is refused.

With ``--funnel-evidence-out`` the run also replays the six pre-registration
v2 §8.5 neighbours and the 2x cost stress, appends each as its own trial, and
writes the ``hermes funnel`` evidence file (#997).

Usage:
    HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_portfolio_backtest.py \\
        --start 2020-09-01 --end 2025-08-31 --performance-out gate/train_val.json \\
        --funnel-evidence-out gate/funnel-evidence.json
    python scripts/backtest/run_portfolio_backtest.py --dry-run   # load bars, exit

Exit codes:
    0 - backtest completed, trial logged, report and performance file saved
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
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from funnel_evidence import FUNNEL_REPLAYS, FunnelReplay, funnel_evidence
from run_calibration import (
    DATA_MANIFEST_JSON,
    FEE_HISTORY_CSV,
    TRIAL_LOG_JSONL,
    _load_manifest,
    _load_verified_bars,
    _parse_iso_date,
    _parse_pair,
    _report_to_json,
    _strategy_parameters,
    _trial_record,
)
from run_calibration_full import _depth_frac, _enabled_roster, _sharpe

from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.backtest.performance import performance_file, round_trips
from hermes.application.backtest.replay import BacktestRun, ExecutionModel, replay
from hermes.application.calibration.harness import CalibrationHarness
from hermes.application.execution.kernel import FillKernel
from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.sizing import SizingPolicy
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillTriggerEvaluator
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.application.strategy_protocol.trial_log import trial_split, trial_summary
from hermes.domain.errors import DataManifestFormatError, DomainError, InfraError, TrialLogError
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.fees.fee_schedule_selector import (
    FeeHistoryOutOfRangeError,
    select_fee_schedule,
)
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.logging import new_correlation_id

_DEFAULT_START: Final = "2020-09-01"
_DEFAULT_END: Final = "2024-06-30"
_RNG_SEED: Final = 0
_BOOTSTRAP_SEED: Final = 0
"""Fixed so the §8.2 interval certify computes is the same on every rerun."""

STUBBED: Final[dict[str, str]] = {
    "RO-02 spread spike": "no order book history; the synthetic book's spread is its own median",
    "RO-03 max slippage": "the synthetic book fills at the §6.2 price",
    "RO-04 stale book": "every synthetic book is fresh",
    "RO-05/RO-06 depeg": "no stablecoin price series",
    "RO-07 macro ATR halt": "not wired in the backtest",
    "RO-08 portfolio drawdown": "§10 drawdown is judged on the equity curve by certify",
    "kill evaluator": "experiment state stubbed at zero drawdown; §10 is judged by certify",
}
"""Pre-registration rules this run does not apply, and why (#822 scope)."""


def _parse_pairs(text: str) -> tuple[Pair, ...]:
    """Parse ``--pairs``: comma-separated symbols inside the signed universe."""
    universe = PreRegistration().universe()
    symbols = [s.strip().upper() for s in text.split(",") if s.strip()]
    outside = [s for s in symbols if s not in universe]
    if not symbols or outside or len(set(symbols)) != len(symbols):
        raise argparse.ArgumentTypeError(
            f"--pairs must list distinct symbols from {list(universe)}, got {text!r}"
        )
    return tuple(_parse_pair(s) for s in symbols)


def _merge(bars: list[Bar]) -> list[Bar]:
    """Order every pair's bars as they close, the daily bars first on ties (#311)."""
    return sorted(bars, key=lambda b: (b.close_time, b.interval != KlineInterval.D1, str(b.pair)))


def _kernel(strategy_id: StrategyId, starting_cash: Money, sizing: SizingPolicy) -> FillKernel:
    """Compose the fill kernel over one portfolio with the signed correlation lock."""
    return FillKernel(
        fill_model=OrderbookWalkFillModel(apply_slippage=False, rng=random.Random(_RNG_SEED)),  # noqa: S311 — deterministic replay, not crypto
        risk_engine=RiskEngine(rules=[CorrelationLock()]),
        kill_evaluator=KillTriggerEvaluator(pre_reg=PreRegistration()),
        tracker=PortfolioTracker(PortfolioSnapshot.seed(strategy_id, starting_cash)),
        strategy_id=strategy_id,
        sizing=sizing,
    )


def _trial_parameters(
    strategy: dict[str, Any], model: ExecutionModel, pairs: tuple[Pair, ...]
) -> dict[str, Any]:
    """Return the strategy parameters plus what makes this run a distinct trial."""
    return {
        **strategy,
        "pairs": ",".join(str(p) for p in pairs),
        "portfolio": "shared-capital",
        "risk.rules": "RO-01",
        "execution.fill_model": "orderbook_walk",
        "execution.entry_bps": model.entry_bps,
        "execution.stop_bps": model.stop_bps,
        "execution.depth_frac": model.depth_frac,
    }


def _entries_by_pair(run: BacktestRun) -> dict[str, int]:
    """Return the filled entries per pair."""
    counts = Counter(str(f.order.pair) for f in run.fills if f.order.side is OrderSide.BUY)
    return dict(sorted(counts.items()))


def _headline(run: BacktestRun, report_json: dict[str, Any]) -> dict[str, Any]:
    """Return the headline counts a trial records."""
    m02 = report_json.get("m02_fill_price_delta_bps") or {}
    return {
        "signals_seen": run.signals_seen,
        "fills_count": len(run.fills),
        "round_trips": len(round_trips(run.fills)),
        "entries_by_pair": ",".join(f"{p}={n}" for p, n in _entries_by_pair(run).items()),
        "declined": ",".join(f"{rule}={n}" for rule, n in sorted(run.declined.items())),
        "m02_avg_bps": m02.get("avg_bps"),
        "m04_rejection_rate": report_json.get("m04_rejection_rate"),
    }


def _payload(
    *,
    pairs: tuple[Pair, ...],
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
        "pairs": [str(p) for p in pairs],
        "strategy_id": final.strategy_id.value,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "fill_model": "orderbook_walk",
        "execution": {
            "entry_bps": str(model.entry_bps),
            "stop_bps": str(model.stop_bps),
            "depth_frac": str(model.depth_frac),
        },
        "signals_seen": run.signals_seen,
        "fills_count": len(run.fills),
        "round_trips": len(round_trips(run.fills)),
        "entries_by_pair": _entries_by_pair(run),
        "rejections_count": len(run.rejections),
        "declined": dict(sorted(run.declined.items())),
        "stubbed": STUBBED,
        "starting_cash": str(final.starting_cash),
        "final_equity": str(final.equity),
        "realized_pnl": str(final.realized_pnl),
        "open_positions": sorted(str(p) for p in final.positions),
        "daily_sharpe": sharpe,
        "daily_equity_points": len(run.daily_equity),
        "datasets": datasets,
        "split": split,
        "report": report_json,
    }


@dataclass(frozen=True, slots=True)
class _Replayed:
    """One replay of the run and what its trial line and outputs need."""

    replay: FunnelReplay
    parameters: dict[str, Any]
    model: ExecutionModel
    run: BacktestRun
    final: PortfolioSnapshot
    report_json: dict[str, Any]
    sharpe: float | None


def _replay_one(
    spec: FunnelReplay,
    bars: list[Bar],
    *,
    parameters: dict[str, Any],
    model: ExecutionModel,
    kernel: FillKernel,
    fees_at: Callable[[datetime, Pair], FeeSchedule],
    pairs: tuple[Pair, ...],
    start: datetime,
    end: datetime,
) -> _Replayed:
    """Replay ``bars`` with one strategy instance per pair on a fresh kernel."""
    strategy_id = kernel.tracker.snapshot.strategy_id
    run = replay(
        bars,
        strategy={pair: SwingStrategyAdapter(**parameters) for pair in pairs},
        kernel=kernel,
        window_start=start,
        window_end=end,
        fees_at=fees_at,
        correlation_id=new_correlation_id,
        model=model,
    )
    label = "Replay" if not spec.trial_fields() else f"Funnel replay {spec.trial_id}"
    print(
        f"{label}: {run.signals_seen} signals, {len(run.fills)} fills, "
        f"{len(round_trips(run.fills))} round trips, declined={dict(run.declined)}"
    )
    report = CalibrationHarness(strategy_id=strategy_id.value).run(
        window_start=start,
        window_end=end,
        fills=run.fills,
        rejections=run.rejections,
        mid_prices=run.reference_prices,
    )
    return _Replayed(
        replay=spec,
        parameters=parameters,
        model=model,
        run=run,
        final=kernel.tracker.snapshot,
        report_json=_report_to_json(report),
        sharpe=_sharpe(run.daily_equity),
    )


async def _load(
    manifest: Any, pairs: tuple[Pair, ...], start: datetime, end: datetime
) -> tuple[list[Bar], list[dict[str, Any]]]:
    """Load every pair's 4h and 1D bars through the manifest."""
    bars: list[Bar] = []
    datasets: list[dict[str, Any]] = []
    for pair in pairs:
        bars_4h, bars_1d, pair_datasets = await _load_verified_bars(manifest, pair, start, end)
        if not bars_4h or not bars_1d:
            raise ValueError(f"no bars for {pair} in window; check HERMES_DATA_DIR and ingest")
        print(f"  {pair}: bars_4h={len(bars_4h)} bars_1d={len(bars_1d)}")
        bars += bars_4h + bars_1d
        datasets += pair_datasets
    return _merge(bars), datasets


async def _run(args: argparse.Namespace) -> int:
    pairs: tuple[Pair, ...] = args.pairs
    start = _parse_iso_date(args.start)
    end = _parse_iso_date(args.end)
    model = ExecutionModel(depth_frac=args.depth_frac)

    try:
        roster = _enabled_roster(Path(args.config_dir))
    except (ConfigError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    strategy_id = StrategyId(roster.name)
    names = ",".join(str(p) for p in pairs)
    print(f"Portfolio backtest: {start.date()} -> {end.date()} ({names}, {strategy_id.value})")
    fee_rows = load_binance_fee_history(FEE_HISTORY_CSV)

    print(f"Loading historical bars, verified against {args.manifest}...")
    try:
        manifest = _load_manifest(Path(args.manifest))
        bars, datasets = await _load(manifest, pairs, start, end)
        split = trial_split(manifest, start, end)
    except (DataManifestFormatError, DomainError, InfraError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("--dry-run set; skipping strategy execution. No trial logged.")
        return 0

    def fees_at(at: datetime, for_pair: Pair) -> FeeSchedule:
        return select_fee_schedule(fee_rows, at, for_pair)

    base_parameters = _strategy_parameters()
    try:
        plan = [
            (spec, spec.parameters(base_parameters))
            for spec in (FUNNEL_REPLAYS if args.funnel_evidence_out else FUNNEL_REPLAYS[:1])
        ]
    except ValueError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    starting_cash = Money(amount=roster.capital_budget, currency=roster.quote)
    try:
        results = [
            _replay_one(
                spec,
                bars,
                parameters=spec_parameters,
                model=spec.model(model),
                kernel=_kernel(strategy_id, starting_cash, roster.sizing_policy()),
                fees_at=spec.fees(fees_at),
                pairs=pairs,
                start=start,
                end=end,
            )
            for spec, spec_parameters in plan
        ]
    except FeeHistoryOutOfRangeError as err:
        print(f"ERROR: fee history does not cover a signal: {err}", file=sys.stderr)
        return 1
    primary = results[0]

    # Every trial is logged before any output exists, so no result exists without its trial.
    trial_log = JsonlTrialLog(Path(args.trial_log))
    try:
        for result in results:
            record = _trial_record(
                strategy_id,
                {
                    **_trial_parameters(result.parameters, result.model, pairs),
                    **result.replay.trial_fields(),
                },
                split,
                start,
                end,
                datasets,
                _headline(result.run, result.report_json),
            )
            trial_log.append(replace(record, sharpe=result.sharpe))
    except (TrialLogError, RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as err:
        print(f"ERROR: trial not logged, nothing written: {err}", file=sys.stderr)
        return 1
    try:
        trials = trial_summary(trial_log, strategy_id=strategy_id.value)
        performance = performance_file(primary.run, trials=trials, bootstrap_seed=_BOOTSTRAP_SEED)
    except (TrialLogError, ValueError) as err:
        print(f"ERROR: trial logged, performance file not written: {err}", file=sys.stderr)
        return 1
    print(f"Trial logged: {args.trial_log} (split={split.value}, trials={trials.count})")

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    base = Path(os.environ.get("HERMES_DATA_DIR", "D:/hermes-data")) / "backtests"
    out_path = Path(args.output) if args.output else base / f"mvp1-portfolio-{ts}.json"
    perf_path = (
        Path(args.performance_out)
        if args.performance_out
        else base / f"mvp1-portfolio-{ts}-performance.json"
    )
    outputs: list[tuple[Path, dict[str, Any]]] = [
        (
            out_path,
            _payload(
                pairs=pairs,
                start=start,
                end=end,
                run=primary.run,
                final=primary.final,
                sharpe=primary.sharpe,
                model=model,
                datasets=datasets,
                split=split.value,
                report_json=primary.report_json,
            ),
        ),
        (perf_path, performance),
    ]
    if args.funnel_evidence_out:
        try:
            evidence = funnel_evidence(
                strategy_id.value,
                {r.replay.trial_id: r.run.daily_equity for r in results},
                trade_count=len(round_trips(primary.run.fills)),
            )
        except ValueError as err:
            print(f"ERROR: trials logged, funnel evidence not written: {err}", file=sys.stderr)
            return 1
        outputs.append((Path(args.funnel_evidence_out), evidence))
    for path, body in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2, sort_keys=True))
    print(f"Report saved: {out_path}")
    print(f"Performance file saved: {perf_path}")
    if args.funnel_evidence_out:
        print(f"Funnel evidence saved: {args.funnel_evidence_out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the backtest."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--pairs",
        type=_parse_pairs,
        default=_parse_pairs(",".join(PreRegistration().universe())),
        help="Comma-separated symbols (default: the signed §3 universe)",
    )
    parser.add_argument("--start", default=_DEFAULT_START, help="Window start (YYYY-MM-DD UTC)")
    parser.add_argument("--end", default=_DEFAULT_END, help="Window end (YYYY-MM-DD UTC)")
    parser.add_argument("--output", default=None, help="Report JSON path (default: auto)")
    parser.add_argument(
        "--performance-out", default=None, help="Certify performance file path (default: auto)"
    )
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
        "--funnel-evidence-out",
        default=None,
        help="Also run the v2 §8.5 neighbour and cost-stress replays, log each as a trial, "
        "and write the hermes funnel evidence file here",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
