# Change: 360-shadow-parity-metrics

**SDD change**: `360-shadow-parity-metrics`
**Date**: 2026-10-03
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#360)

## Summary

Adds calibration metrics M-09 (P&L correlation) and M-10 (Sharpe match),
computed blind. Following the Code Owner's 2026-10-03 rulings, "sim vs real"
in shadow means the live shadow run against a backtest replay of the same
days, and the thresholds are ADR-011 D-19's. A verdict shows only pass, block
or insufficient; its raw value is sealed until certification. The §10 shadow
Sharpe kill shipped in #422; this change reuses its Sharpe convention.

## Scope

### In

- `application/calibration/shadow_parity.py`: `m09_pnl_correlation`,
  `m10_sharpe_match`, `MetricVerdict` with a sealed value.
- `domain/statistics/rng.py`: the SplitMix64 generator, moved out of
  `bootstrap.py` so both bootstraps share it. `percentile` becomes public.

### Out

- Running the metrics on schedule from shadow fills, and acting on a BLOCK:
  after #353 produces shadow fills and the replay path exists.

## Decisions

### D-1: Thresholds from ADR-011 D-19

- **Decision**: M-09 passes when r > 0.85 and the bootstrap 95% CI lower
  bound > 0.70; M-10 passes when the annualised Sharpe gap < 0.3.
- **Rationale**: Code Owner ruling, 2026-10-03 (decisions journal).
- **Cost of reversal**: LOW.

### D-2: Series and sample rules from SA-5, the source ADR-011 cites

- **Decision**: M-09 correlates cumulative net P&L over matched trades
  (SA-5 §1.4), needs 30 matched trades (SA-5 §3.1) and bootstraps 10,000
  resamples of trade pairs (SA-5 §3.2), resorted into time order before
  cumulating. M-10 uses the 30-day rolling Sharpe on daily returns,
  annualised with √365, as the §10 shadow kill does.
- **Rationale**: ADR-011 D-19 cites SA-5 §2 for its thresholds and is silent
  on series and sample size.
- **Cost of reversal**: LOW.

### D-3: Sealed values

- **Decision**: `MetricVerdict` keeps the raw value out of `repr`, `str` and
  equality, and `unseal(certified=...)` raises before certification.
- **Rationale**: automation stays blind (ADR-012 D-06; binding rule 4 of the
  2026-10-03 shadow-P&L decision). Anything rendered from a verdict carries
  the status only.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Verdicts are correct and blind

- **A-1.1**: `uv run pytest tests/unit/application/calibration/test_shadow_parity.py`

## Design

See the module docstring of `shadow_parity.py`.

## Test plan

1. M-09: identical runs pass, opposite runs block, small noise passes, under
   30 trades is insufficient, the same seed gives the same verdict.
2. M-10: identical returns pass, a large gap blocks, under 30 days is
   insufficient.
3. Blindness: `repr`, `str` and a logged verdict never contain the value;
   `unseal` refuses before certification.
4. `bash scripts/dev/pr-preflight.sh` passes.

## Risks

- **Matching shadow and replay trades**: the caller pairs them. A mismatch
  in trade counts raises instead of guessing.
- **Bootstrap time**: 10,000 resamples of a few dozen trades take well under
  a second.
