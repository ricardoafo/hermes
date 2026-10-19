# Change: 822-portfolio-backtest

**SDD change**: `822-portfolio-backtest`
**Date**: 2026-10-06
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/822-portfolio-backtest/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The Train+Validation gate (issue #735) needs the experiment's equity curve:
three pairs on one 5,000 USDT capital with the §5.4/§5.5 limits binding across
them. Until now the backtest ran one pair per invocation on its own capital
with no risk rules. This change lets the replay loop take one strategy per
pair through one kernel, feeds the RO-01 correlation lock the 30-day return
correlation of the daily bars already replayed, adds a portfolio driver, and
writes the `hermes certify --performance` file from the run itself. It also
brings `CorrelationLock` in line with the signed §5.5 text. The main risk is
reading §5.5 wrongly; the reading taken is stated in D-2 and is the one the
Code Owner was asked to confirm.

## Scope

### In

- `application/backtest/replay.py`: `strategy` may be a `{pair: strategy}`
  mapping; daily closes are tracked to compute correlations at each entry;
  the entry's pair is the RO-01 candidate; one equity point per close time;
  declines counted under `correlation-lock` and `exposure-cap`.
- `application/execution/kernel.py`: `MarketContext.correlations`, merged into
  the portfolio state the router evaluates.
- `domain/risk/state.py` + `domain/portfolio/snapshot.py`:
  `PortfolioState.held_pairs`, filled from the open positions.
- `domain/risk/correlation_lock.py`: the signed §5.5 rule (D-2).
- `application/backtest/performance.py`: round trips from fills and the
  certify performance file.
- `scripts/backtest/run_portfolio_backtest.py`: the driver.
- Tests: unit (replay, performance, lock, script helpers), property (lock
  thresholds, round-trip PnL), integration (three generated pairs).

### Out

- Wiring RO-01 into `hermes serve` (issue #514 owns the serve wiring and the
  record-only correlation inputs of #835).
- RO-02..RO-08 in the backtest: no book, peg or macro history; listed in the
  report's `stubbed` block.
- §8.3 walk-forward and purged cross-validation: separate gate work.

## Decisions

### D-1: One kernel, one strategy instance per pair

- **Decision**: `replay()` accepts `Mapping[Pair, StrategyPort]` and routes
  each bar to its pair's strategy; all signals go through the same kernel
  and tracker.
- **Alternatives**: a new portfolio loop (duplicates #706/#707/#723 handling);
  merging per-pair results afterwards (the limits would not bind).
- **Rationale**: serve already runs `dict[Pair, StrategyPort]` over one
  kernel; the backtest now matches it, so backtest/shadow parity holds.
- **Cost of reversal**: LOW.

### D-2: RO-01 as signed

- **Decision**: block a new entry when two held pairs correlate above 0.80
  with each other and the candidate correlates above 0.80 with both.
  Strictly above ("exceeds"); a missing coefficient counts as 0.
- **Alternatives**: the previous code (block any entry correlated at >= 0.80
  with any one held pair) already refuses the second position, which §5.5
  does not say; changing to it would be a §11 deviation. Blocking the third
  entry whenever the two held pairs correlate, whatever the candidate's
  correlation, ignores "third correlated pair".
- **Rationale**: pre-registration v1 §5.5 binds (ADR-014). The Code Owner was
  asked to confirm "As signed"; this change builds that answer.
- **Cost of reversal**: LOW: one predicate.

### D-3: Correlation series

- **Decision**: reuse `domain/portfolio/correlation.py` (#835): Pearson on
  simple daily returns of closed 1D bars, aligned by day; the lock uses a
  value only when a full 30-return window exists.
- **Rationale**: serve records the same series, so a later serve wiring and
  the backtest agree. Only bars already delivered are held: no look-ahead.
- **Cost of reversal**: LOW.

### D-4: Performance file from the run

- **Decision**: the driver writes `window: train_val`, the daily equity, each
  closed round trip's gross PnL and fees, the trial summary of the strategy
  (this run included) and a fixed bootstrap seed. One trial with no Sharpe
  variance writes variance 0 (one trial deflates by nothing); several trials
  without a variance stop the run.
- **Rationale**: nothing is copied by hand between the run and certify.
- **Cost of reversal**: LOW.

### D-5: Trial before output

- **Decision**: the trial is appended before the report or performance file
  is written, as in the per-pair driver.
- **Rationale**: pre-registration §8.1; no result exists without its trial.

## Requirements

### R-1: Shared capital and limits

- **A-1.1**: `uv run pytest tests/integration/backtest/test_portfolio_backtest.py -k exposure`
  shows a third entry refused by the exposure cap.

### R-2: Correlation lock as signed

- **A-2.1**: `uv run pytest tests/unit/domain/risk/test_correlation_lock.py`
- **A-2.2**: `uv run pytest tests/integration/backtest/test_portfolio_backtest.py -k correlat`

### R-3: One equity point per day

- **A-3.1**: `uv run pytest tests/integration/backtest/test_portfolio_backtest.py -k one_point`

### R-4: Certify reads the file

- **A-4.1**: `uv run pytest tests/integration/backtest/test_portfolio_backtest.py -k certify`

### R-5: Driver

- **A-5.1**: `uv run pytest tests/unit/scripts_backtest/test_run_portfolio_backtest.py`
- **A-5.2**: `uv run python scripts/backtest/run_portfolio_backtest.py --dry-run`
  with no data exits 1 with one `ERROR:` line, logging no trial.

## Design

```text
bars (3 pairs, close-time order)
  └─ replay(strategy={pair: SwingStrategyAdapter})
       ├─ tracker.mark(bar); D1 → _DailyCloses.add
       ├─ entry → MarketContext(correlations=_DailyCloses.correlations(),
       │                        market_snapshot.candidate_pair=pair)
       └─ FillKernel → router: position limit → RiskEngine([CorrelationLock()])
                               → sizing (§5.4, §5.5 exposure cap) → fill
performance_file(run, trials=trial_summary(log), bootstrap_seed=0)
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green.

Test tiers touched: unit, property, integration

## Risks

- **Exposure cap at marked value**: the cap is checked at marked value
  (sizing-per-prereg), and a position is marked at its fill price until the
  next bar. Fills are 5 bps over the close (§6.2), so two positions at the
  250 cap mark at 250.125 each and a third entry on the same bar
  (750.25 > 750) is refused. Simultaneous breakouts in all three pairs fill
  two. This follows the existing engine; the integration test pins it.
- **Retried entries**: a refused entry returns the strategy to flat (#707),
  so a breakout that persists signals again on every bar; the declined
  counters count each retry.
- **Gate end date**: `--end 2025-08-31` parses to midnight at the start of
  that day, so the last Validation day is not replayed. Conservative: no
  read reaches 2025-09-01.

## Dependencies on other changes

- Builds on #706, #707, #723 and #680 (all merged) and #835 (merged).
- Issue #735 runs this driver once it merges.
