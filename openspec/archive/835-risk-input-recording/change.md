# Change: 835-risk-input-recording

**SDD change**: `835-risk-input-recording`
**Date**: 2026-10-06
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/835-risk-input-recording/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Serve starts measuring two risk inputs it lacks today and stores them in the
ledger: the rolling 1-hour median spread per pair (RO-02 input) and the
30-day correlation of every two pairs' daily returns (RO-01 input,
pre-registration §5.5). A light sampler beside the bar loop reads one public
depth book per pair every 5 minutes through the existing depth adapter; the
correlations are computed from the stored 1D bars once per completed day.
**No rule reads either value**: wiring RO-01 / RO-02 changes trading and
waits for the Code Owner's ruling (issue #514). The main risk is the sampler
disturbing the bar loop; it is a sibling task that logs and skips every
failure.

## Scope

### In

- `domain/market_data/spread_sample.py`: `SpreadSample`, `median_spread`,
  `SPREAD_MEDIAN_WINDOW` (1 h).
- `domain/portfolio/correlation.py`: `pearson`, `aligned_daily_returns`,
  `PairCorrelation`, `CORRELATION_WINDOW_DAYS` (30).
- `ports/risk_inputs.py`: `RiskInputStorePort` (two writes, two reads).
- `application/risk_inputs/`: `SpreadSampler`, `CorrelationRecorder`.
- Ledger migration `20261006T1200_risk_inputs.sql` (two new tables) and the
  four `SqliteLedger` methods.
- Serve: the sampler as a second task in the run TaskGroup; the recorder
  after each handled 1D bar's `record_live_bar`.
- Import-linter contract `risk-inputs-are-record-only` and a pin test.

### Out

- Wiring RO-01 / RO-02 into `shadow_risk_engine()`, filling
  `PortfolioState.correlations` or `MarketSnapshot.median_spread_1h`
  (issue #514, Code Owner decision).
- Settling `>` vs `>=` at 0.80 and 30-day (§5.5) vs 7-day (ADR-011) for
  RO-01 (issue #514).
- Backfilling correlations from history the ledger never stored (warm-up
  bars are not in `live_bars`).
- The backtest's stubbed inputs (`replay.py`) stay as they are.
- Console / review views of the new tables.

## Decisions

### D-1: A periodic sampler, not the books serve already reads

- **Decision**: Read one depth book per pair every 300 s in a sampler task
  beside the bar loop, through the existing `BinanceDepthSnapshotAdapter`.
- **Alternatives**: (a) record the spread of each book the shadow executor
  reads: it reads books only on signals, a few per week, so a 1-hour median
  would almost never have more than one sample; (b) a depth diff stream:
  far more code and traffic for a value no rule reads yet.
- **Rationale**: 5 minutes gives 12 samples per hour, enough for a median a
  single odd book cannot move. A depth request (limit 20 ≤ 100) weighs 5, so
  3 pairs cost 15 weight per 5 minutes, 3 per minute on average: 0.5 % of
  the adapter's 600/min token bucket and 0.05 % of Binance's 6000/min IP
  limit. The sampler shares the bucket with the executor, so the two can
  never exceed it together.
- **Cost of reversal**: LOW — one task and one constant.

### D-2: Store each sample with the median it closes

- **Decision**: Each `spread_samples` row carries the spread and the median
  of the pair's samples in `[book_time - 1 h, book_time]`, plus their count.
- **Alternatives**: store samples only and compute the median at read time:
  correct, but the value a future rule would have seen is then not on
  record.
- **Rationale**: Recording the exact input a rule would read makes a later
  replay or audit of RO-02 straightforward. The median is read back from the
  ledger, so it survives restarts.
- **Cost of reversal**: LOW.

### D-3: Correlation definition (pre-registration §5.5)

- **Decision**: Pearson correlation of simple daily close-to-close returns
  of closed 1D bars, over the 30 returns (31 closes) ending at the newest
  bar; a return counts only when both pairs have both closes exactly one
  day apart. Each row stores `window_days = 30` and `sample_count`.
- **Alternatives**: log returns (indistinguishable at daily crypto moves,
  harder to check by hand); 4H bars (§5.5 says "30-day", and daily returns
  are the conventional series for that window); require a full window
  before recording (would record nothing for 30 days on a fresh ledger).
- **Rationale**: §5.5 names the window ("30-day rolling correlation") but
  not the series; this is the plainest reading. Recording the sample count
  leaves the minimum a future rule requires to the wiring decision.
- **Cost of reversal**: LOW — nothing reads the rows; a new definition gets
  a new `window_days` or a new table.

### D-4: Compute once per day, when the last pair's 1D bar is stored

- **Decision**: After each handled 1D bar, the recorder reads every pair's
  window; if any pair lacks that day's bar it records nothing, so the last
  pair's bar of the day records the values, keyed on the bar's close time.
- **Rationale**: The 1D bars of all pairs close together but arrive one by
  one; computing on the first would miss the newest return of the others.
  `INSERT OR IGNORE` on `(pair_a, pair_b, window_days, as_of)` makes a
  replay after restart write nothing.
- **Cost of reversal**: LOW.

### D-5: Off the critical path

- **Decision**: A sample failure (any exception, or 30 s without an answer)
  is logged and skipped; the sampler waits one full step before its first
  round, and shutdown cuts a round in flight. A correlation failure is
  logged and skipped inside `handle`.
- **Rationale**: Neither value drives a decision, so neither may stop serve
  or delay a bar. The journal gets counts and error types, never values.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Spread samples are recorded beside the bar loop

- **A-1.1**: `uv run pytest -o addopts="" tests/unit/application/risk_inputs/test_spread_sampler.py` exits 0.
- **A-1.2**: `uv run pytest -o addopts="" tests/integration/entrypoints/test_serve_risk_inputs.py` exits 0
  (samples for every pair; a failing depth source leaves exit 0 and every bar processed).

### R-2: Pair correlations are recorded per completed day

- **A-2.1**: `uv run pytest -o addopts="" tests/unit/domain/portfolio/test_correlation.py tests/unit/application/risk_inputs/test_correlation_recorder.py` exits 0.

### R-3: Additive storage

- **A-3.1**: `uv run python scripts/dev/migration-sql-check.py` exits 0.
- **A-3.2**: `uv run pytest -o addopts="" tests/unit/adapters/ledger/test_sqlite_ledger_risk_inputs.py` exits 0.

### R-4: Nothing reads the values

- **A-4.1**: `uv run lint-imports` keeps `risk-inputs-are-record-only`.
- **A-4.2**: `uv run pytest -o addopts="" tests/unit/application/risk_inputs/test_risk_inputs_unwired.py` exits 0
  (`correlations == {}`, no RO-01 / RO-02 in serve's engine, median still the current spread).

## Design

```sql
CREATE TABLE IF NOT EXISTS spread_samples (
    pair_base TEXT NOT NULL, pair_quote TEXT NOT NULL,
    book_time TEXT NOT NULL, sampled_at TEXT NOT NULL,
    spread TEXT NOT NULL, median_spread_1h TEXT NOT NULL,
    samples_1h INTEGER NOT NULL, recorded_at TEXT NOT NULL,
    PRIMARY KEY (pair_base, pair_quote, book_time));
CREATE TABLE IF NOT EXISTS pair_correlations (
    pair_a TEXT NOT NULL, pair_b TEXT NOT NULL, window_days INTEGER NOT NULL,
    as_of TEXT NOT NULL, correlation REAL NOT NULL, sample_count INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (pair_a, pair_b, window_days, as_of));
```

```python
class RiskInputStorePort(Protocol):
    def record_spread_sample(self, sample: SpreadSample) -> bool: ...
    def spread_samples(
        self, pair: Pair, start: datetime, end: datetime
    ) -> tuple[SpreadSample, ...]: ...
    def record_pair_correlation(self, correlation: PairCorrelation) -> bool: ...
    def pair_correlations(self, start: datetime, end: datetime) -> tuple[PairCorrelation, ...]: ...
```

Serve: `run()` adds `group.create_task(_sample_spreads(composition, shutdown))`;
`_BarLoop.handle` calls `_record_correlations(bar)` right after
`record_live_bar`. `Composition.spread_sample_seconds` (default 300) lets
tests shorten the step.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. Full unit + integration suite and `tests/e2e` green.

Test tiers touched: unit, property, integration

## Risks

- **Sampler takes depth budget from an execution**: shared token bucket;
  15 weight per 5 minutes against a 600/min bucket leaves the executor's
  one request per signal unaffected. The first round waits one full step,
  so boot catch-up keeps the bucket to itself.
- **Short windows on a fresh ledger**: warm-up bars are not stored, so early
  rows cover fewer than 30 returns; `sample_count` says so, and the wiring
  decision must set a minimum.
- **A rule starts reading the values by accident**: the import-linter
  contract and the pin test fail on any such import or rule change.

## Dependencies on other changes

- Issue #514 (re-scope) holds the wiring decision for RO-01 / RO-02.
