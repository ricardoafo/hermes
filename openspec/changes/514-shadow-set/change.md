# Change: 514-shadow-set

**SDD change**: `514-shadow-set`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/514-shadow-set/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Issue #514 asked which of the live-phase risk and kill inputs serve should
act on. The Code Owner chose the shadow set: shadow runs what the backtest
that gated the strategy runs, so the shadow can be checked against it. The
portfolio backtest already runs RO-01 (correlation lock, #822) and
pre-registration v2 §5.5 binds it, so serve now runs RO-01 on the same 30-day
correlations of the 1D closes, computed by one shared class. RO-02 and the
live-phase recovery, single SA-9 and 30-trade Sharpe kills stay off, since
the backtest never runs them; their inputs stay recorded (#835). The main
risk is a serve-only difference in the closes RO-01 sees, which the daily
parity check now covers.

## Scope

### In

- `DailyCloses` moves from the backtest replay to
  `hermes.domain.portfolio.correlation`, shared by the replay and serve.
- `ShadowExecutor.observe` feeds every pair's 1D close to it, and entries
  get its correlations; `shadow_risk_engine` adds `CorrelationLock`.
- The daily parity check seeds its replay with the 1D closes before the day,
  so its RO-01 sees what serve's did.
- The `risk-inputs-are-record-only` import contract keeps the recorded rows
  out of every decision and stops forbidding the shared estimator module.
- Docstrings and the pin tests state the shadow set.

### Out

- RO-02 (spread spike), the 90-day recovery kill, the single SA-9 P0 kill
  and the 30-trade Sharpe kill in serve: off by the ruling.
- A view of the recorded inputs: follow-up PR on the same issue.
- Restart persistence of the peak equity: already shipped (#828, #863),
  covered by `test_restart_restores_the_mark_to_market_peak_equity`.

## Decisions

### D-1: RO-01 reads in-process closes, not the recorded rows

- **Decision**: Serve computes the correlations from the 1D bars the
  executor observes, with the class the backtest replay uses.
- **Alternatives**: read the latest `pair_correlations` row (a second code
  path with its own staleness and sample-count rules, and a ledger read per
  entry).
- **Rationale**: one estimator, one window rule (30 aligned returns or the
  pair pair is left out) and one source of bars order for both sides, so
  shadow cannot drift from the backtest on RO-01 by construction.
- **Cost of reversal**: LOW — drop the rule from `shadow_risk_engine`.

### D-2: The parity replay carries the closes across its one-bar replays

- **Decision**: `replay` takes an optional `closes`, as it takes `entries`;
  the parity check seeds one with the warm-up's 1D bars.
- **Alternatives**: feed the parity replay the recorded rows (then parity
  would not check serve's computation).
- **Rationale**: without it the parity replay sees no correlations and calls
  every RO-01 refusal a mismatch.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Serve refuses a third correlated entry as the backtest does

- **A-1.1**: `test_serve_refuses_a_third_entry_correlated_with_two_held_pairs` passes.
- **A-1.2**: `test_serve_and_the_backtest_replay_read_the_same_correlations` passes.

### R-2: The parity check agrees with serve on an RO-01 refusal

- **A-2.1**: `test_ro01_refuses_in_the_replay_on_the_closes_before_the_day_as_in_serve` passes.

### R-3: The shadow set is pinned

- **A-3.1**: `tests/unit/application/risk_inputs/test_risk_inputs_unwired.py` passes:
  RO-01 and RO-03 to RO-08 run, RO-02 does not, the live-only kill inputs are neutral.
- **A-3.2**: `uv run lint-imports` keeps `risk-inputs-are-record-only`.

## Design

- `hermes.domain.portfolio.correlation.DailyCloses`: `add(bar)` keeps a
  bounded tail of 1D closes per pair; `correlations()` returns Pearson on
  the aligned simple daily returns of every two pairs with a full 30-return
  window.
- `ShadowExecutor.observe(bar)`: every 1D bar goes to `DailyCloses`; BTC/USDT
  1D closes still feed RO-07. `execute` passes
  `correlations=self._daily_closes.correlations()` for entries.
- `replay(..., closes: DailyCloses | None = None)`; `check_day` builds one
  from the warm-up's 1D bars in delivery order and passes it to every call.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. Full suite: `pytest tests/unit tests/integration tests/e2e -n auto`.

## Risks

- A serve that misses 1D bars (outage) holds older closes than the backtest
  would; the alignment rule drops returns across gaps, the same in both.
- RO-01 only fires with three positions open, and the §5.5 exposure cap
  usually refuses a third full position first; the rule still matters when
  sizing leaves room.
