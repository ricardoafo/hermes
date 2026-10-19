# Change: 354-certification-statistics

**SDD change**: `354-certification-statistics`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#354)

Mirror of engram topic key `sdd/354-certification-statistics/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Computes the statistics that pre-registration v1 §8 and §9 require, so the
certification gates from #382 can pass on real evidence instead of failing
closed. It adds:

- the Deflated Sharpe Ratio in its published form;
- a circular block bootstrap CI on Sharpe;
- the six §9 secondary metrics;
- expanding walk-forward splits and purged combinatorial CV splits.

All of it is pure, stdlib-only domain code with no new dependency. `hermes
certify --performance FILE` computes a window's §9 metrics from a daily equity
curve, the trade list and the trial statistics. Main risk: a formula error
certifies a strategy that should fail, so the DSR is pinned to the published
worked example.

## Scope

### In

- `domain/statistics/sharpe.py`: per-period Sharpe, sample skewness and
  non-excess kurtosis, expected maximum Sharpe under the null (SR0), PSR and
  DSR (§8.1).
- `domain/statistics/bootstrap.py`: circular block bootstrap percentile CI on
  Sharpe, 10,000 resamples, 30-day blocks, explicit seed (§8.2).
- `domain/statistics/performance.py`: max drawdown, time to recover from
  drawdowns of 8% or more, ulcer index, profit factor, fees over gross PnL,
  win rate (§9).
- `domain/statistics/splits.py`: expanding walk-forward (6-month step, 3-month
  test) and purged combinatorial CV (5 groups, 5-day purge, 3-day embargo)
  (§8.3).
- `application/calibration/window_metrics.py`: assembles the gate's
  `WindowMetrics` from those functions.
- `hermes certify --performance FILE`.
- Backtest gate: drop the OOS-only trade count and bootstrap CI rows (D-7).

### Out

- Running backtests and producing the equity curve and trades: #313.
- Logging trials and computing their count and Sharpe variance: #357. Until
  then the performance file supplies them. Without them the DSR is not
  computed, so its row fails closed.
- Sim-vs-live drift metrics (KS, Wasserstein) for the shadow gate: a follow-up.

## Decisions

### D-1: Stdlib only, in the domain layer

- **Decision**: implement with `math` and `statistics.NormalDist`; no numpy
  or scipy.
- **Alternatives**: numpy and scipy, as the draft statistical framework
  suggests. That means two heavy dependencies and an ADR-010 supply-chain
  review, and nothing here needs vector speed: 10,000 resamples of about 500
  days run in seconds.
- **Rationale**: the formulas are short. Pure functions in `domain/` fit the
  hexagonal rules and are easy to property-test.
- **Cost of reversal**: LOW.

### D-2: DSR uses the published formula, not the draft's

- **Decision**: SR0 = √V · ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N·e))), with
  γ the Euler–Mascheroni constant and V the variance of the trials' Sharpe
  ratios. DSR = Φ((SR̂ − SR0) √(T − 1) / √(1 − γ̂₃ SR̂ + (γ̂₄ − 1)/4 · SR̂²)),
  with γ̂₄ the non-excess kurtosis and SR̂ per period. With one trial, SR0 = 0
  and the DSR equals the PSR against zero.
- **Alternatives**: the formula in the draft `statistical-framework.md` §4.1.
  It puts ln(N) inside the variance term and does not deflate for trials the
  way the paper does.
- **Rationale**: pre-registration §8.1 names the paper. Its worked example
  (T = 1250, annualised SR 2.5, skew −3, kurtosis 10, N = 100, V = 0.5/250)
  gives SR0 ≈ 0.1132 per day and DSR ≈ 0.90. A test pins both values.
- **Cost of reversal**: LOW.

### D-3: Bootstrap is circular-block with a percentile interval

- **Decision**: circular block bootstrap, 30-day blocks, 10,000 resamples,
  two-sided 95% percentile interval on per-period Sharpe. Block starts come
  from a seeded SplitMix64 generator, specified in the code, so a seed
  reproduces the interval exactly on any Python version.
- **Rationale**: §8.2 fixes the block length and resample count. The circular
  variant gives every observation equal weight. Sharpe's sign does not depend
  on annualisation, so the "lower bound > 0" rule is scale-free.
- **Cost of reversal**: LOW.

### D-4: Unrecovered drawdowns count as infinite recovery time

- **Decision**: a drawdown of 8% or more that is still open at the window's
  end has infinite recovery time, so the §9 row fails. This follows the
  existing `CertificationResult.recovery_days` docstring.
- **Alternatives**: count the days until the window ends, which is more
  lenient.
- **Rationale**: this is the stricter reading. **Code Owner's call.**
- **Cost of reversal**: LOW.

### D-5: CPCV holds out 2 of 5 groups

- **Decision**: 5 groups with 2 test groups per split, giving C(5,2) = 10
  splits. Purging drops the 5 training days before each test group, and the
  embargo drops the 3 days after it.
- **Rationale**: §8.3 says "5-fold" without saying how many groups each split
  tests. 2 is the smallest number that makes the scheme combinatorial, and the
  draft framework uses it. **Code Owner's call.**
- **Cost of reversal**: LOW.

### D-6: Degenerate inputs never produce a passing number

- **Decision**: a statistic that cannot be computed returns `None`, or
  infinity where the sign is clear, so a gate fails closed instead of passing
  on an invented value. Cases: fewer than two returns, zero variance, no
  trades. Profit factor with no losing trades is infinite and passes; the fee
  ratio with gross PnL of zero or less is infinite and fails.
- **Cost of reversal**: LOW.

### D-7 (DECIDED by the Code Owner, 2026-10-03): trade count and bootstrap CI are OOS-only

- **Decision**: the backtest gate from #382 no longer applies the §9 trade
  count (≥ 30) and bootstrap CI (lower bound > 0) rows to Train+Val; both
  still bind the one-shot OOS test. This answers the open question in #382
  (change `355-certification-gates` D-2).
- **Cost of reversal**: LOW.

## Requirements

### R-1: DSR matches the published example

- **A-1.1**: `uv run pytest tests/unit/domain/statistics/test_sharpe.py`

### R-2: Every §9 metric is computed from a performance file

- **A-2.1**: `uv run pytest tests/unit/application/calibration/test_window_metrics.py tests/unit/entrypoints/test_certify.py`

### R-3: Splits never leak test data into training

- **A-3.1**: `uv run pytest tests/unit/domain/statistics/test_splits.py`.
  Property tests check that train and test sets are disjoint and keep the
  purge and embargo gaps.

## Design

```text
src/hermes/domain/statistics/__init__.py
src/hermes/domain/statistics/sharpe.py        sharpe, skewness, kurtosis, expected_max_sharpe, psr, dsr
src/hermes/domain/statistics/bootstrap.py     block_bootstrap_sharpe_ci
src/hermes/domain/statistics/performance.py   max_drawdown, recovery_days, ulcer_index, profit_factor, fees_to_gross_pnl, win_rate
src/hermes/domain/statistics/splits.py        walk_forward_splits, purged_cpcv_splits
src/hermes/application/calibration/window_metrics.py   compute_window_metrics -> WindowMetrics
src/hermes/entrypoints/certify.py             --performance FILE
```

Performance file, with one equity point per UTC day:

```json
{
  "window": "train_val",
  "daily_equity": [5000.0, 5012.5, 4998.1],
  "trades": [{"gross_pnl": 41.0, "fees": 2.1}],
  "trials": {"count": 12, "sharpe_variance": 0.0004},
  "bootstrap_seed": 20261003
}
```

`window` is `train_val`, which fills the backtest metrics, or `oos`, which
fills the OOS metrics. The computed metrics replace the same section of
`--evidence`.

## Test plan

1. Every acceptance criterion above passes.
2. Property tests: the DSR falls as N rises, the bootstrap gives the same
   result for the same seed, drawdown stays in [0, 1], and splits stay
   disjoint and keep their gaps.
3. `bash scripts/dev/pr-preflight.sh` passes.

## Risks

- **A formula error certifies a bad strategy**: the DSR is pinned to the
  published example, and every metric has a hand-computed test.
- **Trial statistics are operator-supplied until #357**, so an understated
  trial count inflates the DSR. The performance file's SHA-256 is recorded
  with the transition.
- **Pure-Python bootstrap speed**: a few seconds for 10,000 resamples of a
  16-month window. That is acceptable for a command run a handful of times.
