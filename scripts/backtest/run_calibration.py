#!/usr/bin/env python3
"""MVP1 backtest driver — Phase 1 (composition-root wiring + smoke test).

Wires the shipped spec chain into an end-to-end backtest:

  BinanceHistoricalAdapter  (spec-4c) — reads Vision klines from
                                        $HERMES_DATA_DIR
  SwingStrategyAdapter      (spec-5)  — Donchian(20)+ADX(14)+EMA(200)+
                                        Chandelier(22, 3.0)
  fee_schedule_selector     (#260)    — point-in-time VIP0 + zero-fee BTC
                                        promo (2022-07-28 -> 2023-03-21)
  CalibrationHarness        (spec-6)  — collects fills, emits M-01..M-07

Phase 1 scope — deliberately narrow (see openspec change
``backtest-driver-mvp1-phase1``):

- Single pair (default BTC/USDT), 4h + 1D timeframes.
- Single window (default 2024-01-01 .. 2024-03-31 — inside pre-reg
  Train slice).
- Fills synthesised directly from strategy signals (entry_px =
  bar.close, exit_px = exit-bar.close). No OrderRouter, no
  fill kernel, no orderbook. Phase 2 (``run_calibration_full.py``)
  wires them.
- Report saved as JSON under $HERMES_DATA_DIR/backtests/mvp1-<ts>.json.

Usage:
    python scripts/backtest/run_calibration.py                   # default window
    python scripts/backtest/run_calibration.py --pair ETHUSDT
    python scripts/backtest/run_calibration.py \\
        --start 2024-06-01 --end 2024-09-30
    python scripts/backtest/run_calibration.py --output /tmp/report.json
    python scripts/backtest/run_calibration.py --dry-run          # enumerate + exit

Exit codes:
    0 - backtest completed, report saved
    1 - fatal error (missing data, adapter failure, etc.)
    2 - usage error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from hermes.adapters.data_manifest.json_file import JsonFileDatasetManifest
from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.adapters.market_data.binance_historical import BinanceHistoricalAdapter
from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.calibration.harness import CalibrationHarness
from hermes.application.strategy_protocol.pinned_dataset import (
    PinnedBars,
    assert_manifest_matches_preregistration,
    load_pinned_bars,
)
from hermes.application.strategy_protocol.strategy_factory import strategy_kind
from hermes.application.strategy_protocol.trial_log import trial_split
from hermes.domain.calibration.certification import CertificationState
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.errors import DataManifestFormatError, DomainError, TrialLogError
from hermes.domain.fees.fee_schedule_selector import (
    FeeHistoryOutOfRangeError,
    select_fee_schedule,
)
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.dataset_manifest import DatasetManifest, DataSplit
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.risk.state import PortfolioState
from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.project_root import load_pre_registration

FEE_HISTORY_CSV = Path("docs/experiments/swing-mvp1/fee-history.csv")
DATA_MANIFEST_JSON = Path("docs/experiments/swing-mvp1/data-manifest.json")
TRIAL_LOG_JSONL = Path("docs/experiments/swing-mvp1/trial-log.jsonl")

# Phase-1 defaults (deliberately narrow — see docstring).
_DEFAULT_PAIR = "BTCUSDT"
_DEFAULT_START = "2024-01-01"
_DEFAULT_END = "2024-03-31"
_DEFAULT_STARTING_EQUITY_USD = Decimal("10000")

# The EMA(200) regime filter is seeded from the oldest closes of the
# strategy's 300-bar daily buffer, so its value depends on how many daily
# bars it has seen until that buffer is full (issue #880). Serve warms up
# with 300 daily bars; loading 300 days before the window makes the
# backtest's first decisions match serve's.
_WARMUP_DAYS = 300


def _parse_pair(sym: str) -> Pair:
    """Parse a Binance-style concatenated symbol (BTCUSDT) into a Pair."""
    if sym.upper() == "BTCUSDT":
        return Pair(base=Currency.BTC, quote=Currency.USDT)
    if sym.upper() == "ETHUSDT":
        return Pair(base=Currency.ETH, quote=Currency.USDT)
    if sym.upper() == "SOLUSDT":
        return Pair(base=Currency.SOL, quote=Currency.USDT)
    raise ValueError(f"unsupported symbol {sym!r} — Phase 1 handles BTCUSDT/ETHUSDT/SOLUSDT")


def _parse_iso_date(s: str) -> datetime:
    """Parse a YYYY-MM-DD string into a UTC-aware datetime at 00:00:00."""
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _load_manifest(path: Path) -> DatasetManifest:
    """Load the data manifest and refuse it unless it matches the signed pre-registration."""
    manifest = JsonFileDatasetManifest(path).load()
    pre_reg = load_pre_registration()
    assert_manifest_matches_preregistration(
        manifest, experiment_id=pre_reg.experiment_id(), windows=pre_reg.data_windows()
    )
    return manifest


def _datasets_payload(*loads: PinnedBars) -> list[dict[str, Any]]:
    """Return the manifest entries behind ``loads``, one per dataset id."""
    entries = {e.dataset_id: e for load in loads for e in load.entries}
    return [
        {"dataset_id": e.dataset_id, "content_sha256": e.content_sha256, "row_count": e.row_count}
        for _, e in sorted(entries.items())
    ]


def _synthesise_fill(
    signal: StrategySignal,
    pair: Pair,
    fee_bps: int,
    starting_equity: Decimal,
    strategy_id: StrategyId,
) -> Fill:
    """Build a Fill from a strategy signal at bar.close price.

    Phase 1 shortcut — Phase 2 (``run_calibration_full.py``) replaces this
    with the ``FillKernel`` pipeline (OrderRouter, orderbook walk).

    Sizing: 1% risk per trade against ATR-based stop distance, capped
    at 5% of equity per pre-reg §5.4. When the strategy signal doesn't
    carry an ATR (e.g. exit signal without engines populated), fall
    back to a fixed 5% notional.
    """
    entry_px = signal.entry_price if signal.entry_price is not None else Decimal("0")
    if entry_px <= 0:
        raise ValueError(f"signal.entry_price must be > 0 for Fill synthesis, got {entry_px!r}")
    # Notional = min(5% of equity, ATR-based 1%-risk notional).
    hard_cap_notional = starting_equity * Decimal("0.05")
    if signal.atr_context is not None and signal.atr_context > 0:
        # T-1 formula: notional = (equity * risk_pct) / (stop_atr_mult * atr / entry_px)
        stop_atr_mult = Decimal("1.5")  # pre-reg §5.4 default
        risk_pct = Decimal("0.01")  # 1% risk per trade
        stop_distance_frac = (stop_atr_mult * signal.atr_context) / entry_px
        atr_notional = (starting_equity * risk_pct) / stop_distance_frac
        notional = min(hard_cap_notional, atr_notional)
    else:
        notional = hard_cap_notional
    qty_amount = (notional / entry_px).quantize(Decimal("0.00000001"))
    order = Order(
        side=OrderSide.BUY if signal.kind == SignalKind.ENTRY else OrderSide.SELL,
        pair=pair,
        qty=Quantity(amount=qty_amount, asset=pair.base),
        strategy_id=strategy_id,
    )
    fee_amount = (qty_amount * entry_px * Decimal(fee_bps) / Decimal(10000)).quantize(
        Decimal("0.00000001")
    )
    return Fill(
        order=order,
        executed_qty=qty_amount,
        avg_price=entry_px,
        fee=Money(amount=fee_amount, currency=pair.quote),
        timestamp=signal.signal_time,
    )


def _report_to_json(report: Any) -> dict[str, Any]:
    """Best-effort conversion of a CalibrationReport dataclass to JSON-safe dict."""
    if is_dataclass(report) and not isinstance(report, type):
        d = asdict(report)
    elif isinstance(report, dict):
        d = report
    else:
        # Fall back to attribute enumeration.
        d = {k: v for k, v in vars(report).items() if not k.startswith("_")}
    return _jsonify(d)


def _merge_by_close_time(bars_4h: list[Bar], bars_1d: list[Bar]) -> list[Bar]:
    """Merge the 4h and 1D streams in the order their bars close.

    Ordering by ``open_time`` would deliver each 1D bar right after the day's
    first 4h bar, 20 hours before it closes (issue #311).  On equal
    ``close_time`` the 1D bar goes first so the 4h bar closing at midnight
    sees that day's regime, matching the live bar stream.
    """
    return sorted(
        bars_4h + bars_1d,
        key=lambda b: (b.close_time, b.interval != KlineInterval.D1),
    )


def _jsonify(obj: Any) -> Any:
    """Recursively convert Decimal/datetime/enum/tuple/set to JSON-native types."""
    if isinstance(obj, dict):
        return {str(k): _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value") and hasattr(obj, "name"):
        # Enum-ish
        return obj.value if not callable(obj.value) else obj.name
    if is_dataclass(obj) and not isinstance(obj, type):
        return _jsonify(asdict(obj))
    return obj


def _strategy_parameters() -> dict[str, Any]:
    """Return every parameter of the strategy kind at its default, so the trial records what ran."""
    return strategy_kind(None).default_parameters()


def _git_state() -> tuple[str, bool]:
    """Return the HEAD commit and whether tracked files differ from it."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git not found; a trial must record the commit it ran on")
    sha = subprocess.run(  # noqa: S603 — git resolved via shutil.which, fixed arguments
        [git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(  # noqa: S603 — git resolved via shutil.which, fixed arguments
        [git, "status", "--porcelain", "--untracked-files=no"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sha, bool(status.strip())


def _trial_record(
    strategy_id: StrategyId,
    parameters: dict[str, Any],
    split: DataSplit,
    start: datetime,
    end: datetime,
    datasets: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> TrialRecord:
    """Build the trial record for this run."""
    git_sha, git_dirty = _git_state()
    return TrialRecord(
        recorded_at=datetime.now(UTC),
        strategy_id=str(strategy_id),
        parameters={k: str(v) for k, v in parameters.items()},
        split=split,
        window_start=start,
        window_end=end,
        datasets=tuple(DatasetRef(d["dataset_id"], d["content_sha256"]) for d in datasets),
        metrics={k: str(v) for k, v in metrics.items()},
        # The phase-1 driver has no per-period return series; #313 adds the Sharpe.
        sharpe=None,
        git_sha=git_sha,
        git_dirty=git_dirty,
    )


async def _load_verified_bars(
    manifest: DatasetManifest, pair: Pair, start: datetime, end: datetime
) -> tuple[list[Bar], list[Bar], list[dict[str, Any]]]:
    """Load 4h and 1d bars for ``[warm-up, end)`` through the data manifest."""
    adapter = BinanceHistoricalAdapter.create()
    # Load a warm-up buffer BEFORE the trading window so the strategy's
    # EMA(200) + other indicators are populated by the time we start
    # counting signals. Nothing before Train is pinned, so warm-up stops there.
    warmup_start = max(start - timedelta(days=_WARMUP_DAYS), manifest.splits[DataSplit.TRAIN].start)
    # No persisted certification state yet (#355): a backtest is UNTRUSTED, so OOS is refused.
    loads = [
        await load_pinned_bars(
            adapter,
            manifest,
            pair=pair,
            interval=interval,
            start=warmup_start,
            end=end,
            state=CertificationState.UNTRUSTED,
            now=datetime.now(UTC),
        )
        for interval in (KlineInterval.H4, KlineInterval.D1)
    ]
    bars_4h = list(loads[0].bars)
    bars_1d = list(loads[1].bars)
    return bars_4h, bars_1d, _datasets_payload(*loads)


async def _run(args: argparse.Namespace) -> int:
    pair = _parse_pair(args.pair)
    start = _parse_iso_date(args.start)
    end = _parse_iso_date(args.end)

    print(f"Backtest window: {start.date()} -> {end.date()} (pair={args.pair})")
    print("Loading fee history...")
    fee_rows = load_binance_fee_history(FEE_HISTORY_CSV)
    print(f"  loaded {len(fee_rows)} Binance rows")

    print(f"Loading historical bars from Binance Vision, verified against {args.manifest}...")
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

    merged = _merge_by_close_time(bars_4h, bars_1d)

    parameters = _strategy_parameters()
    strategy = strategy_kind(None).create(parameters)
    portfolio_state = PortfolioState(
        peak_value=_DEFAULT_STARTING_EQUITY_USD,
        current_value=_DEFAULT_STARTING_EQUITY_USD,
        realized_slippage_cumulative=Decimal("0"),
    )

    strategy_id = StrategyId(f"swing-mvp1-phase1-{args.pair.lower()}")
    fills: list[Fill] = []
    mid_prices: list[Decimal] = []
    signals_seen = 0

    for bar in merged:
        try:
            signal = strategy.on_bar(bar, portfolio_state)
        except Exception as err:
            print(
                f"WARN: strategy.on_bar raised at {bar.open_time.isoformat()}: {err}",
                file=sys.stderr,
            )
            continue
        if signal is None:
            continue
        # Only count signals that fire INSIDE the trading window — the
        # warm-up buffer's job is to populate indicators, not to trade.
        if signal.signal_time < start:
            continue
        signals_seen += 1
        try:
            fee_schedule = select_fee_schedule(fee_rows, signal.signal_time, pair)
        except FeeHistoryOutOfRangeError as err:
            print(f"WARN: fee history OOR at {signal.signal_time}: {err}", file=sys.stderr)
            continue
        fee_bps = int(fee_schedule.tiers[0].taker_bps)
        try:
            fill = _synthesise_fill(
                signal, pair, fee_bps, _DEFAULT_STARTING_EQUITY_USD, strategy_id
            )
        except ValueError as err:
            print(f"WARN: fill synthesis failed at {signal.signal_time}: {err}", file=sys.stderr)
            continue
        fills.append(fill)
        mid_prices.append(fill.avg_price)

    print(f"Strategy pass: {signals_seen} signals, {len(fills)} fills synthesised")

    if not fills:
        print(
            "WARN: no fills — strategy emitted no signals in the window. Report will be trivial.",
            file=sys.stderr,
        )

    harness = CalibrationHarness(strategy_id=str(strategy_id))
    report = harness.run(
        window_start=start,
        window_end=end,
        fills=fills,
        rejections=[],
        mid_prices=mid_prices,
    )

    # Every run that evaluated the strategy is a trial (pre-registration §8.1). The trial is
    # logged before the report exists, so no report can exist without its trial.
    report_json = _report_to_json(report)
    headline = {
        "signals_seen": signals_seen,
        "fills_count": len(fills),
        "m01_fill_count": report_json.get("m01_fill_count"),
        "m04_rejection_rate": report_json.get("m04_rejection_rate"),
        "m05_orphan_position_count": report_json.get("m05_orphan_position_count"),
    }
    trial_log = JsonlTrialLog(Path(args.trial_log))
    try:
        trial_log.append(
            _trial_record(strategy_id, parameters, split, start, end, datasets, headline)
        )
    except (TrialLogError, RuntimeError, OSError, subprocess.CalledProcessError) as err:
        print(f"ERROR: trial not logged, report not written: {err}", file=sys.stderr)
        return 1
    print(f"Trial logged: {args.trial_log} (split={split.value})")

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    if args.output:
        out_path = Path(args.output)
    else:
        base = Path(os.environ.get("HERMES_DATA_DIR", "D:/hermes-data"))
        out_path = base / "backtests" / f"mvp1-{args.pair.lower()}-{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "pair": args.pair,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "signals_seen": signals_seen,
        "fills_count": len(fills),
        "datasets": datasets,
        "split": split.value,
        "report": report_json,
    }
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
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
