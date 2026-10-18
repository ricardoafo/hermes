# Change: v2-funnel-evidence

**SDD change**: `v2-funnel-evidence`
**Date**: 2026-10-17
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#997)

Mirror of engram topic key `sdd/v2-funnel-evidence/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`scripts/backtest/run_portfolio_backtest.py --funnel-evidence-out PATH` writes
the evidence file `hermes funnel` reads (`v2-backtest-funnel` Design §2). With
the flag, the driver also runs the replays pre-registration v2 §8.5 binds to
every backtest run: six one-parameter neighbours and one replay at twice the
fees and slippage. Every replay is appended to the trial log as its own trial,
in the existing format, before any output is written. Without the flag the
driver does exactly what it does today. Main risk: a neighbour or stress
replay that silently differs from what §8.5 signed (wrong parameter, costs not
scaled) would judge v2 on the wrong family; the replay grid is a constant
pinned by tests to the pre-registration's numbers.

## Scope

### In

- `scripts/backtest/funnel_evidence.py`: the §8.5 replay grid, the cost-stress
  execution model and fee schedule, the extra trial fields of a funnel replay,
  and the evidence body.
- `scripts/backtest/run_portfolio_backtest.py`: `--funnel-evidence-out PATH`.
- `tests/unit/scripts_backtest/test_funnel_evidence.py` and a synthetic
  end-to-end run in `tests/unit/scripts_backtest/test_run_portfolio_backtest.py`
  whose evidence passes `hermes funnel`'s parser.
- `changelog.d/997-added.md`.

### Out

- **`running_strategies`.** Written as `{}`: no strategy runs once v1 is
  retired (pre-registration v2 §10, last row), and the driver has no
  Train + Validation replay of another strategy to correlate with. A later
  change that runs a second strategy fills it from that strategy's replay.
- **`backtest.yml`.** The workflow step that passes the flag and runs
  `hermes funnel` is a separate PR.
- **Strategy selection and v2 exit pricing.** Issue #979 adds them to the same
  driver. This change only overrides strategy parameters and scales execution
  costs, so it works on whatever strategy and execution model the driver loads.

## Decisions

### D-1: The replay grid is a constant, not a CLI input

- **Decision**: `FUNNEL_REPLAYS` lists, in order, the base run, the six §8.5
  neighbours (`donchian_period` 15 and 25, `adx_threshold` 20 and 30,
  `chandelier_atr_mult` 2.5 and 3.5, each the base with one parameter
  replaced) and the cost stress (base parameters, costs × 2). The flag runs
  all eight; there is no way to run a subset.
- **Alternatives**: read the grid from the pre-registration front matter (the
  `backtest_funnel` block has no grid key, and adding one is a deviation under
  §11); CLI flags per neighbour (a run could drop a bad neighbour, the risk
  `v2-backtest-funnel` names).
- **Rationale**: §8.5 states the grid in prose and ADR-021 §5 says ablations
  are logged as trials; a fixed constant makes "every neighbour ran and was
  logged" true by construction.
- **Cost of reversal**: LOW.

### D-2: Neighbours take §8.5's values, not offsets from the base

- **Decision**: a neighbour sets the parameter to the value §8.5 names
  (Donchian 15, not "base − 5"). A neighbour whose value equals the loaded
  base, or names a parameter the strategy does not take, refuses the run
  before any replay.
- **Rationale**: §8.5 names values. For the base the driver loads today
  (Donchian 20, ADX 25, multiple 3.0, the §5 values) both readings agree; the
  refusal catches a base for which they would not.
- **Cost of reversal**: LOW.

### D-3: Cost stress multiplies every bps the execution model has, and the fees

- **Decision**: the stress replay's execution model has every `*_bps` field
  of the loaded model × 2 (today `entry_bps` and `stop_bps`, so the synthetic
  book's touch is twice as far from the reference) and every fee tier's maker
  and taker bps × 2. `depth_frac` is not a cost and is unchanged.
- **Rationale**: §6.2 "every fee and slippage above × 2". Scaling by field
  name keeps the stress correct when another change adds a bps field to the
  model.
- **Cost of reversal**: LOW.

### D-4: Trial lines of funnel replays carry two extra fields; the base does not

- **Decision**: a neighbour's and the stress replay's trial parameters add
  `funnel.replay` (its trial id, below); the stress replay also adds
  `execution.cost_multiplier: 2` (fees are not otherwise visible on the
  line). The base run's trial line is the same as a run without the flag.
  Each line is appended with the existing `_trial_record`, its own headline
  metrics and its own Sharpe.
- **Rationale**: the base run is the same trial whether or not the funnel
  replays ran beside it; a neighbour line names its replay, so the trial log
  shows a missing one.
- **Cost of reversal**: LOW.

### D-5: Trial ids name the replay

- **Decision**: the ids in `trials` and `neighbour_trial_ids` are `base`,
  `<parameter>=<value>` for a neighbour (`donchian_period=15`) and
  `cost_stress=2`. They equal the `funnel.replay` field of the trial line.
- **Alternatives**: the trial line's chain hash. The trial-log port returns
  no hash on append, and reading it back would tie the driver to the JSONL
  adapter's layout.
- **Cost of reversal**: LOW.

### D-6: `trials` is the CSCV matrix: the base run and its six neighbours

- **Decision**: `trials` holds the daily returns of the base run and the six
  neighbours. The cost-stress replay is a trial line in the log and the
  `cost_stress` section of the evidence, but not a column of `trials`.
- **Rationale**: §8.5 "The CSCV trial matrix for PBO is the base run plus its
  six neighbours". The stress run is the same parameters at other costs, not a
  configuration the strategy could have been selected as.
- **Cost of reversal**: LOW.

### D-7: The evidence days and the trade count

- **Decision**: day `i` of `daily_equity` is the UTC day whose 1D close
  produced point `i` (the bar's own day: a close at `23:59:59.999` or at the
  next midnight both date the day that ends there). `start_date` is the first
  point's day. Every replay must produce points on the same close times, one
  per consecutive day, or the evidence is not written (exit 1, trials already
  logged). `trade_count` is the base run's round trips, the trades certify's
  performance file counts.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The flag writes evidence that `hermes funnel` parses and validates

- **A-1.1**: `uv run pytest tests/unit/scripts_backtest/test_run_portfolio_backtest.py -k evidence`
  runs the driver on synthetic bars with the flag; `load_funnel_evidence`
  and `validate_evidence` (thresholds from the signed v2 front matter) accept
  the file, and `hermes funnel` exits 0 or 1, never 3.

### R-2: Every replay is a trial line

- **A-2.1**: the same run appends eight trial lines: the base, six neighbours
  and the stress replay, each with its parameters.

### R-3: Without the flag nothing changes

- **A-3.1**: a synthetic run without the flag appends one trial and writes no
  evidence file.

### R-4: The replay grid and cost stress are the pre-registered ones

- **A-4.1**: `uv run pytest tests/unit/scripts_backtest/test_funnel_evidence.py`

## Design

```text
scripts/backtest/funnel_evidence.py
  COST_STRESS_MULTIPLIER = 2
  FUNNEL_REPLAYS: tuple[FunnelReplay, ...]          base, 6 neighbours, cost stress
  FunnelReplay(trial_id, overrides, cost_multiplier)
    .parameters(base) -> dict                       refuses unknown or no-op override
    .model(base) -> ExecutionModel                  every *_bps × cost_multiplier
    .fees(fees_at) -> fees_at                       every tier's bps × cost_multiplier
    .trial_fields() -> dict                         {} for the base
  funnel_evidence(strategy_id, curves, trade_count) -> dict   Design §2 body

scripts/backtest/run_portfolio_backtest.py --funnel-evidence-out PATH
  replays = FUNNEL_REPLAYS if flag else FUNNEL_REPLAYS[:1]
  every replay -> run -> trial line (append, in order)
  base run -> report + performance file (unchanged)
  flag -> evidence file
```

The evidence body: `strategy_id`, `start_date`, `daily_equity` (base),
`trade_count` (base round trips), `cost_stress` (`multiplier: 2.0`, the stress
replay's equity), `trials` (D-6), `neighbour_trial_ids` (the six, in §8.5
order) and `running_strategies: {}`.

The trial count the performance file reports includes every replay logged by
the run, so the DSR deflates by 1 + 6 + 1 more trials per flagged run
(`v2-backtest-funnel` Design §5.3).

## Test plan

1. Every acceptance criterion above passes.
2. `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`,
   `uv run lint-imports` clean.
3. Synthetic data only: no test reads `/srv`, `HERMES_DATA_DIR` or any kline.

Test tiers touched: unit, property

## Risks

- **Eight replays per run.** A five-year three-pair replay takes seconds;
  eight take eight times that. Acceptable for a workflow run.
- **Validation touches.** One flagged Validation run is one touch (§4
  "including the neighbour and cost stress replays"), but writes eight
  Validation trial lines; `validation_count` counts lines, not touches.
- **#979 merge.** That change edits `_trial_parameters` and how the model is
  built. This change only calls them, so a merge keeps both.

## Dependencies on other changes

- `v2-backtest-funnel` (#957, merged): the evidence format and parser.
- Issue #979: strategy selection and v2 exit pricing in the same driver.
