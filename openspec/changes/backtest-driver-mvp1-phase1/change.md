# Change: backtest-driver-mvp1-phase1

**SDD change**: `backtest-driver-mvp1-phase1`
**Date**: 2026-08-26
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#269)

Mirror of engram topic key `sdd/backtest-driver-mvp1-phase1/change`.

## Summary

Ships the composition-root script that wires together the 9 delivered specs (spec-1..spec-9) into an end-to-end MVP1 backtest. Phase 1 is deliberately slim — reads Binance Vision klines via `BinanceHistoricalAdapter`, feeds a `SwingStrategyAdapter`, synthesises fills at `bar.close` using the point-in-time fee-history selector, and emits a `CalibrationReport` with M-01..M-07 metrics. The full `OrderRouter` → `SimulatedExchangeAdapter` pipeline (with orderbook walk + slippage + rejections) lands in Phase 2 as a separate spec. Verifiable end-to-end today; ships a JSON report the operator can inspect.

## Scope

### In

- **NEW**: `scripts/backtest/run_calibration.py` (~280 LOC) — Phase 1 driver:
  - Argparse CLI: `--pair`, `--start`, `--end`, `--output`, `--dry-run`.
  - Loads klines lazily via `BinanceHistoricalAdapter.stream_bars` for `KlineInterval.H4` and `KlineInterval.D1`.
  - Loads fee history from `docs/experiments/swing-mvp1/fee-history.csv`.
  - Warms up strategy indicators with a 250-day pre-window buffer (EMA(200) + Donchian(20) 4h + ADX(14) + Chandelier(22)).
  - Feeds merged bar stream to `SwingStrategyAdapter` with pre-reg §5.1 params (defaults).
  - Synthesises `Fill` per signal: `entry_px = signal.entry_price`, `qty = min(5% equity, T-1 ATR-sized notional)`, `fee_bps = select_fee_schedule(rows, signal.signal_time, pair).tiers[0].taker_bps`.
  - Runs `CalibrationHarness.run(fills, rejections=[], mid_prices=[fill.avg_price ...])` and emits the report as JSON.
  - Only counts signals whose `signal_time >= window_start` (warm-up bars don't trade).
- **NEW**: `scripts/backtest/__init__.py`.
- **NEW**: `tests/unit/scripts_backtest/test_run_calibration.py` (8 tests) — covers pure helpers (`_parse_pair`, `_parse_iso_date`, `_synthesise_fill`, `_jsonify`) including zero-fee promo window and hard-cap-vs-ATR notional selection.
- **NEW**: `changelog.d/wave-backtest-driver-mvp1-phase1.md`.

### Out (Phase 2 — separate spec)

- **`OrderRouter` + `SimulatedExchangeAdapter` wiring**: replaces the direct `_synthesise_fill` shortcut with the full T-1 pipeline (sizing + risk overlay + kill trigger + orderbook walk + slippage + rejections). Requires a synthetic `Orderbook` per bar (Vision has no L2), which is a design decision worth its own change.
- **Full backtest windows**: 2020-09..2024-06 Train + 2024-07..2025-08 Validate + 2025-09..2026-12 OOS (pre-reg §4). Phase 1 handles arbitrary windows via `--start`/`--end`; Phase 2 wires the multi-window walk.
- **Multi-pair walk**: BTC + ETH + SOL simultaneously with correlation lock (SA-9). Phase 1 handles one pair at a time.
- **Statistical framework**: DSR, block bootstrap CI, CPCV, walk-forward, Bonferroni (pre-reg §8). Owns its own spec.
- **HTML report generation**: JSON is the machine-readable primary; a report renderer is a follow-up.

## Decisions

### D-1: Phase 1 skips the `OrderRouter` → `SimulatedExchangeAdapter` pipeline

- **Decision**: `_synthesise_fill` builds a `Fill` directly from a `StrategySignal` at `bar.close`. No `OrderRouter.route()` call.
- **Alternatives**: (a) full pipeline wire in Phase 1; (b) mock `OrderRouter.route()` to return `Fill(...)` unchanged.
- **Rationale**: Full pipeline requires synthesising `Orderbook`, `MarketSnapshot`, `ExperimentState`, `SizingInput`, `PortfolioState` per bar — each with subtle correctness constraints (positive spreads, non-empty depth, monotone timestamps). Getting those right is a substantial design task that deserves its own spec. Phase 1's shortcut proves the OUTER wiring (bars → strategy → fee schedule → CalibrationHarness → JSON report) works. Phase 2 swaps in the real pipeline once its design is settled.
- **Cost of reversal**: LOW — `_synthesise_fill` is one function; replacing it is mechanical.

### D-2: 250-day warm-up buffer BEFORE the trading window

- **Decision**: `warmup_start = start - timedelta(days=250)`. Bars in `[warmup_start, start)` are fed to the strategy but signals firing in that range are IGNORED.
- **Alternatives**: (a) require the operator to specify a warm-up-inclusive window; (b) 200-day (exact EMA period) buffer; (c) 400-day buffer.
- **Rationale**: EMA(200) needs 200 daily bars minimum. Donchian(20) + ADX(14) + Chandelier(22) all use the 4h buffer which fills faster. 250 days = 200 + 50-day margin covers boundary cases (missing bars at data-ingest edges, holiday gaps in some exchanges). Not asking the operator means the CLI is simpler: they specify the trading window they want, driver handles warm-up transparently.
- **Cost of reversal**: LOW — swap the constant.

### D-3: Fill quantity is the MIN of hard-cap and ATR-sized notional

- **Decision**: `notional = min(0.05 * equity, (equity * 0.01) / (1.5 * atr / entry_px))`.
- **Alternatives**: (a) always ATR-sized (uncapped); (b) always 5% notional (flat); (c) full sizing calculator delegation.
- **Rationale**: Pre-reg §5.4 mandates the hard cap (5% equity per position). Phase 1's shortcut sizing has to respect it. ATR-based sizing gives more realistic notional when volatility is high; the hard cap prevents oversizing on very-low-ATR bars (unlikely but possible). Phase 2 delegates to `SizingCalculator` which owns the full formula.
- **Cost of reversal**: LOW — `_synthesise_fill` is replaced entirely in Phase 2.

### D-4: JSON output includes both `report` and top-level metadata

- **Decision**: `{"generated_at": ..., "pair": ..., "window_start": ..., "window_end": ..., "signals_seen": ..., "fills_count": ..., "report": {...}}`.
- **Alternatives**: (a) `report` only; (b) separate metadata file.
- **Rationale**: The wrapper lets an operator confirm at a glance what window ran without opening the nested report. `signals_seen` vs `fills_count` distinguishes signals-generated from signals-actually-fillable (in Phase 1 they're always equal — Phase 2 introduces rejections that make them diverge).
- **Cost of reversal**: LOW.

## Requirements

### R-1: Driver runs to completion on a 3-month window against real Vision data

- **A-1.1**: `HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_calibration.py --pair BTCUSDT --start 2024-01-01 --end 2024-03-31` exits 0.
- **A-1.2**: A JSON file is created under `$HERMES_DATA_DIR/backtests/mvp1-<pair>-<ts>.json`.
- **A-1.3**: The JSON payload contains `report.m01_fill_count == fills_count`.

### R-2: Unit tests cover the pure helpers

- **A-2.1**: `pytest tests/unit/scripts_backtest/test_run_calibration.py` — 8 tests exit 0.
- **A-2.2**: Zero-fee promo case (`fee_bps=0` → `fill.fee.amount == 0`) is included.
- **A-2.3**: Hard-cap-vs-ATR notional selection is asserted (5% cap wins when ATR is small relative to entry).

### R-3: Warm-up ignores signals BEFORE window_start

- **A-3.1**: The `signals_seen` counter increments only when `signal.signal_time >= window_start`.

### R-4: Fee schedule is time-aware per signal

- **A-4.1**: For a signal at 2022-10-15 (inside the promo window), the fee_bps used is 0 for BTC pairs and 10 for ETH/SOL — inherited from the fee-history selector's contract (verified by that spec's own tests; this driver only forwards the value).

### R-5: Dry-run mode

- **A-5.1**: `--dry-run` loads bars + fee history but skips strategy execution; exits 0 with a "skipping strategy execution" line.

## Design

Module layout:

```text
scripts/backtest/
├── __init__.py
└── run_calibration.py          # ~280 LOC
tests/unit/scripts_backtest/
├── __init__.py
└── test_run_calibration.py     # 8 tests
```

Data flow diagram:

```mermaid
flowchart LR
  Vision[Binance Vision archives<br/>$HERMES_DATA_DIR/klines/] --> A[BinanceHistoricalAdapter]
  A -->|4h + 1D bars| M[merge by timestamp]
  M --> S[SwingStrategyAdapter<br/>Donchian(20)+ADX(14)+EMA(200)]
  S -->|StrategySignal| F[_synthesise_fill]
  CSV[fee-history.csv] --> L[load_binance_fee_history]
  L --> Sel[select_fee_schedule<br/>rows, signal_time, pair]
  Sel -->|fee_bps| F
  F -->|Fill| H[CalibrationHarness.run]
  H -->|CalibrationReport| J[JSON output<br/>$HERMES_DATA_DIR/backtests/]
```

## Test plan

1. `uv run pytest tests/unit/scripts_backtest/ -v` — 8 tests exit 0.
2. `HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_calibration.py` — smoke run against real data, produces JSON report.
3. `HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_calibration.py --dry-run` — exits 0 without running strategy.
4. `bash scripts/dev/pr-preflight.sh --render-body backtest-driver-mvp1-phase1` — all 17 checks green (with `SKIP_PYTEST=1` if consolidator flakes — see #265).

## Risks

- **R1** — Phase 1 shortcut fills don't model slippage → M-02 always shows 0 bps. The report is not comparable to real-money trading yet. Mitigation: explicitly named "Phase 1" in the driver's docstring, changelog, and PR body. Phase 2 replaces `_synthesise_fill`.
- **R2** — Warm-up buffer's 250 days can straddle a pre-reg window boundary. Mitigation: the trading window's `signal_time >= start` filter enforces the boundary regardless of warm-up size.
- **R3** — CalibrationReport's field names may change if `spec-6` evolves. Mitigation: `_report_to_json` uses `asdict` (dataclass-aware), so any field addition/removal flows through automatically.
- **R4** — Hypothesis flake in the consolidator tests (unrelated — see #265) blocks pre-flight. Mitigation: `SKIP_PYTEST=1` bypass documented; the flake fix is a separate ongoing item.

## Dependencies on other changes

- Predecessor: PR #263 (fee-history selector) — this driver imports `select_fee_schedule` + `load_binance_fee_history`. Merged.
- Predecessor: spec-4c (`BinanceHistoricalAdapter`), spec-5 (`SwingStrategyAdapter`), spec-6 (`CalibrationHarness`) — all merged in the overnight session.
- Follow-up: `backtest-driver-mvp1-phase2` (new spec) — swaps `_synthesise_fill` for the full `OrderRouter` → `SimulatedExchangeAdapter` pipeline. Requires synthetic-orderbook design.
