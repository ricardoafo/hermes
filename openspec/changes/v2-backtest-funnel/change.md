# Change: v2-backtest-funnel

**SDD change**: `v2-backtest-funnel`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#957)

Mirror of engram topic key `sdd/v2-backtest-funnel/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Computes the eight ADR-021 §5 backtest funnel criteria that no code checks
yet, and prints one `PASS <check>` / `FAIL <check>` line per criterion in the
`hermes certify` line format. A new sibling command, `hermes funnel`, reads
every threshold from a `backtest_funnel:` block in the YAML front matter of the
strategy's signed pre-registration and refuses when any threshold is missing.
It reads the backtest outputs from one evidence JSON file and records nothing.
The statistics (CSCV probability of backtest overfitting, the Sharpe t-statistic,
per-period returns) are pure stdlib domain functions with property tests. `hermes
certify`, its v1 gate and every §9 row are unchanged. Main risk: a wrong PBO
formula would let an overfit v2 into shadow, so PBO is pinned to hand-computed
examples and to properties that hold by construction.

## Scope

### In

- `domain/statistics/overfitting.py`: CSCV and the probability of backtest
  overfitting (Bailey, Borwein, López de Prado and Zhu, 2015).
- `domain/statistics/sharpe.py`: `sharpe_t_statistic` (SR·√T, the t-statistic
  of the mean return).
- `domain/statistics/period_returns.py`: return over an index range, calendar
  year returns, return over a dated stretch, annualised return.
- `application/calibration/backtest_funnel.py`: `FunnelThresholds`,
  `FunnelEvidence`, `parse_funnel_thresholds` (front matter mapping to typed
  thresholds, refusing on any missing or invalid key) and `evaluate_funnel`
  (one `GateCheck` per criterion).
- `entrypoints/funnel.py` and `entrypoints/commands/funnel.py`: `hermes funnel`.
- `changelog.d/957-added.md`.

### Out

- **§9 rows and DSR.** They stay with `hermes certify` as today. Reading a v2
  pre-registration's own §9 thresholds and DSR ≥ 0.95 into certify, and the
  "implied independent trials" N of ADR-012 D-11, belong to the multi-strategy
  certify work (#361), not here. Certify still accepts only the v1 strategy id.
- **Producing the evidence file.** `scripts/backtest/run_portfolio_backtest.py`
  gains a cost multiplier, dated output and a neighbour grid in a follow-up;
  see Design §5 for exactly what it and `backtest.yml` must produce.
- **Workflow edits.** `.github/` is not touched; the needed inputs and steps are
  listed in Design §5 and are queued separately.
- **Recording.** The funnel does not write the certification store. Making
  `BACKTEST_CERTIFIED` depend on it for v2 lands with the multi-strategy certify.
- **Validation budget, shadow slots, FDR, Romano–Wolf / SPA.** Other ADR-021 §5
  bullets, not part of the backtest funnel.

## Decisions

### D-1: A sibling command, not a new certify gate row

- **Decision**: `hermes funnel <strategy_id> --pre-registration FILE --evidence
  FILE --expect-sha256 HEX` evaluates the funnel and prints its own report. `hermes certify` is
  untouched.
- **Alternatives**: add the funnel rows to certify's `UNTRUSTED →
  BACKTEST_CERTIFIED` gate behind a flag. That edits the v1 gate code path,
  and certify only accepts the v1 strategy id, which ADR-021 §5 does not bind.
- **Rationale**: the task forbids changing any v1 metric, threshold or §9
  check. A separate command cannot change v1's outcome by construction. Issue
  #957 allows "a sibling command used by `backtest.yml`".
- **Cost of reversal**: LOW. `evaluate_funnel` returns `GateCheck`s, so a later
  gate can append them.

### D-2: Thresholds live in the pre-registration's YAML front matter

- **Decision**: a `backtest_funnel:` mapping in the front matter of the signed
  pre-registration file. The command refuses unless `status: SIGNED`, refuses
  any missing, unknown or out-of-range key, and prints the file's SHA-256 so the
  verdict is tied to the exact document. `--expect-sha256 HEX` is required
  (review of PR #964): without it the run is a usage error (exit 2), and a hash
  that differs from the file refuses (exit 1). The caller passes the signed hash
  recorded for the pre-registration (its lab notebook entry), so an edited or
  swapped file can never be judged. The comparison ignores case.
- **Strategy binding** (review of PR #964): the front matter carries a
  top-level `strategy_id`, beside `experiment_id` and outside
  `backtest_funnel`, because it names what the whole document binds, not a
  threshold. The command refuses (exit 1, `REFUSED: ...`) when it is missing or
  differs from the command's `strategy_id` argument, so thresholds signed for
  one strategy never judge another. The `backtest_funnel` block is checked
  first, so v1 still refuses with "has no backtest_funnel block".
- **Alternatives**: a separate JSON thresholds file (two documents to sign);
  `config/` (an AI-writable path under ADR-012 D-08, and not signed);
  constants in `PreRegistration` (hard-coded, which #957 rules out).
- **Rationale**: ADR-021 §5: "Each pre-registration states its exact numbers."
  The front matter is already machine-read (`experiment_id`) and is inside the
  signed hash.
- **Cost of reversal**: LOW.

### D-3: Key names say the comparison

- **Decision**: `_max` means ≤, `_min` means ≥, `_above` means >, `_below`
  means <, following the ADR-021 §5 wording: PBO "≤", t-statistic "above",
  trades "at least", Validation "at least half", correlation "below", and
  "most years" as strictly more than the stated share.
- **Ranges** (review of PR #964), each refused outside its bound:
  `pbo_max` in [0, 1]; `t_stat_above` > 0 (a non-positive bar would pass a
  losing strategy); `cost_stress_multiplier` >= 1;
  `positive_year_share_above` in [0, 1); `validation_to_train_return_min` and
  `neighbour_sharpe_ratio_min` in (0, 1] (0 would pass any positive
  Validation or neighbour, and above 1 would demand more than the base run,
  which is not what "at least half" or "also pass" mean);
  `return_correlation_below` in (-1, 1]; `trade_count_min` a positive integer;
  `cscv_blocks` an even integer from 2 to 20.
- **Rationale**: test rule 4 pins every limit at the limit; the key name makes
  the direction reviewable in the pre-registration itself.
- **Cost of reversal**: LOW.

### D-4: CSCV uses Sharpe per block, average ranks and λ ≤ 0 as overfit

- **Decision**: the T × N matrix of per-period trial returns is cut into S
  contiguous blocks (S even, from `cscv_blocks`; sizes differ by at most one
  row when S does not divide T). For each of the C(S, S/2) halves, the trial
  with the best in-sample Sharpe (first on a tie) is ranked out of sample with
  average ranks, ω = rank / (N + 1), λ = ln(ω / (1 − ω)). PBO is the share of
  halves with λ ≤ 0. A block where a trial's Sharpe is undefined (zero
  dispersion) ranks that trial lowest. Block sums and sums of squares are
  precomputed, so S = 16 and 50 trials over five years runs in seconds.
- **Alternatives**: mean return instead of Sharpe (the paper allows any
  metric; Sharpe matches the DSR); λ < 0 (would not count the median as
  overfit, the lenient reading).
- **Rationale**: the paper's definition. Counting λ = 0 as overfit is the
  stricter reading. **Code Owner's call.**
- **Cost of reversal**: LOW.

### D-5: The t-statistic is SR·√T on the daily returns of the base run

- **Decision**: t = mean / (s / √T) over the T daily returns of the Train +
  Validation run, s the sample standard deviation.
- **Alternatives**: per-trade returns (fewer, noisier observations; the
  evidence has no per-trade return series).
- **Rationale**: the Harvey–Liu–Zhu "t above 3" bar is on the mean return,
  and daily returns are what the DSR already uses.
- **Cost of reversal**: LOW.

### D-6: How the return-based criteria are read

- **Decision**:
  - *Profitable at 2× costs*: the stressed run's equity ends above where it
    starts, and the run's declared multiplier is at least `cost_stress_multiplier`.
  - *Most years*: the share of calendar years in the window with a positive
    return is above `positive_year_share_above`. Partial first and last years
    count as years.
  - *Bull and bear stretches*: the pre-registration lists dated stretches
    (`regimes`, each `kind: bull|bear`). Two checks: the compounded return over
    all bull stretches is positive, and the same over all bear stretches. At
    least one stretch of each kind is required, and every stretch must lie
    inside the evidence window.
  - *Validation at least half of Train*: annualised (365-day) returns; passes
    iff Train is positive and Validation ≥ `validation_to_train_return_min` ×
    Train. The split day is the pre-registration's `train_end`.
  - *Neighbours also pass*: every neighbour trial's Sharpe is at least
    `neighbour_sharpe_ratio_min` × the base run's Sharpe, the base Sharpe is
    positive, and at least one neighbour is given.
  - *Correlation*: the largest Pearson correlation of daily returns with any
    running strategy is below `return_correlation_below`. Signed, not absolute:
    a negatively correlated strategy diversifies. An explicit empty list of
    running strategies passes; a missing one fails.
- **Rationale**: ADR-021 §5 says these six are conventions without a literature
  threshold, so each reading is written down here for the Code Owner. Partial
  years, aggregated stretches and signed correlation are the choices most
  likely to be vetoed. **Code Owner's call.**
- **Cost of reversal**: LOW, one function each.

### D-7: The funnel refuses evidence that reaches the OOS window

- **Decision**: the pre-registration's `oos_start` is required. Evidence whose
  last day is on or after it is an evidence error (exit 3) before any check
  runs.
- **Rationale**: ADR-023 §4 layers; one more costs one comparison.
- **Cost of reversal**: LOW.

### D-8: Same line format and exit codes as certify

- **Decision**: `PASS  <name:<24> observed <x:<22> required <rule>`, header
  `# hermes funnel <sid>: <experiment_id> (pre-registration sha256 <hex>)`,
  footer `FUNNEL PASSED` / `FUNNEL FAILED`, `REFUSED: <reason>` on a refusal.
  Exit 0 passed, 1 failed or refused, 2 usage, 3 pre-registration file or
  evidence error. Check names are single tokens, so the workflow's
  `awk '{print $1, $2}'` keeps name and verdict only.
- **Cost of reversal**: LOW.

### D-9: Overflow and non-finite statistics fail closed

- **Decision** (review of PR #964): every check runs inside one guard. A
  statistic that cannot be computed, raises `OverflowError` (squaring a huge
  return in a Sharpe, `(1 + r) ** (365 / days)` over a tiny stretch) or comes
  out as `inf`/`nan` fails its check with `not computed`; `evaluate_funnel`
  never raises. `annualised_return` raises `ValueError` on overflow or a
  non-finite rate instead of returning `inf`.
- **Rationale**: a crash would lose the other nine verdicts, and an `inf` that
  compares as "above the limit" would pass a check on nonsense.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Every funnel criterion has a check and a PASS/FAIL line

- **A-1.1**: `uv run pytest tests/unit/application/calibration/test_backtest_funnel.py`
  (one test per criterion at its limit and just past it).
- **A-1.2**: `uv run pytest tests/unit/entrypoints/test_funnel.py` prints ten
  PASS lines and `FUNNEL PASSED` for a passing synthetic run.

### R-2: Thresholds come from the pre-registration; a missing one refuses

- **A-2.1**: `uv run pytest tests/unit/application/calibration/test_backtest_funnel.py -k missing`
- **A-2.2**: `uv run pytest tests/unit/entrypoints/test_funnel.py -k refuse`

### R-3: PBO and the t-statistic match hand-computed values and their properties

- **A-3.1**: `uv run pytest tests/unit/domain/statistics/test_overfitting.py tests/unit/domain/statistics/test_sharpe.py`

### R-4: No v1 behaviour changes

- **A-4.1**: `uv run pytest tests/unit/entrypoints/test_certify.py tests/unit/application/calibration/test_certification_gates.py`
  passes unchanged.
- **A-4.2**: `git diff origin/main -- src/hermes/entrypoints/certify.py src/hermes/application/calibration/certification_gates.py src/hermes/application/strategy_protocol/preregistration.py`
  is empty.

### R-5: The job summary still shows no observed numbers

- **A-5.1**: piping `hermes funnel` output through the workflow's
  `awk '/^(PASS|FAIL) /{print $1, $2}'` leaves only names and verdicts
  (asserted in `test_funnel.py`).

## Design

### 1. Pre-registration front matter

```yaml
---
experiment_id: <id>
strategy_id: <strategy id>    # must equal hermes funnel's strategy_id argument
status: SIGNED
backtest_funnel:
  train_end: 2024-06-30          # last Train day; Validation starts the day after
  oos_start: 2025-09-01          # evidence must end before this day
  cscv_blocks: 16                # even, >= 2
  pbo_max: 0.05                  # PBO <= this
  t_stat_above: 3.0              # t > this; must be > 0
  trade_count_min: 100           # Train + Validation trades >= this
  cost_stress_multiplier: 2.0    # fees and slippage multiplier of the stress run (>= 1)
  positive_year_share_above: 0.5 # share of positive calendar years > this
  validation_to_train_return_min: 0.5  # in (0, 1]
  neighbour_sharpe_ratio_min: 0.5      # in (0, 1]
  return_correlation_below: 0.7
  regimes:
    - {kind: bull, start: 2020-10-01, end: 2021-11-10}
    - {kind: bear, start: 2021-11-11, end: 2022-12-31}
---
```

Values here are examples; each v2 pre-registration states its own.

### 2. Evidence file (`hermes funnel --evidence`)

```json
{
  "strategy_id": "<id>",
  "start_date": "2020-09-01",
  "daily_equity": [5000.0, 5003.1, "... one point per UTC day, Train + Validation"],
  "trade_count": 140,
  "cost_stress": {"multiplier": 2.0, "daily_equity": ["... same days as daily_equity"]},
  "trials": {"<trial_id>": ["... daily returns, len(daily_equity) - 1 each"]},
  "neighbour_trial_ids": ["<trial_id>", "..."],
  "running_strategies": {"<strategy_id>": ["... daily returns over the same days"]}
}
```

`strategy_id` must equal the command's argument and `start_date` is the day of
`daily_equity[0]`. Every series must cover the same days; a length mismatch, an
unknown key, a neighbour id not in `trials`, a non-positive equity or a last day
on or after `oos_start` is an evidence error (exit 3). An absent optional
section (`trade_count`, `cost_stress`, `trials`, `neighbour_trial_ids`,
`running_strategies`) fails the checks that need it, as certify does.

### 3. Checks, in output order

| Name | Requirement printed | Passes iff |
| --- | --- | --- |
| `pbo` | `<= pbo_max (CSCV, S blocks)` | PBO ≤ `pbo_max` |
| `t_stat` | `> t_stat_above` | SR·√T > `t_stat_above` |
| `trade_count` | `>= trade_count_min` | trades ≥ `trade_count_min` |
| `cost_stress` | `return > 0 at >= Mx costs` | multiplier ≥ M and return > 0 |
| `positive_years` | `share > p` | positive years / years > p |
| `bull_return` | `> 0 over n bull stretches` | compounded > 0 |
| `bear_return` | `> 0 over n bear stretches` | compounded > 0 |
| `validation_vs_train` | `>= r x Train (annualised)` | Train > 0 and Val ≥ r·Train |
| `neighbours` | `Sharpe >= r x base, every neighbour` | ≥ 1 neighbour, base > 0, min ratio ≥ r |
| `correlation` | `< c with every running strategy` | max ρ < c, or none running |

A statistic that cannot be computed, overflows or is not finite prints
`not computed` and fails (D-9).

### 4. Modules

```text
src/hermes/domain/statistics/overfitting.py      probability_of_backtest_overfitting(matrix, blocks) -> float
src/hermes/domain/statistics/period_returns.py   range_return, calendar_year_returns, stretch_return, annualised_return
src/hermes/domain/statistics/sharpe.py           + sharpe_t_statistic(returns) -> float | None
src/hermes/application/calibration/backtest_funnel.py
src/hermes/entrypoints/funnel.py                 main, load_funnel_evidence, read_front_matter
src/hermes/entrypoints/commands/funnel.py        COMMAND = Command(name="funnel", ...)
```

### 5. What `backtest.yml` and the driver need (queued separately)

Verified against the tree on 2026-10-17. `uv run python
scripts/backtest/run_portfolio_backtest.py --help` lists only `--pairs`,
`--start`, `--end`, `--output`, `--performance-out`, `--dry-run`,
`--trial-log`, `--manifest`, `--depth-frac` and `--config-dir`: no cost
multiplier, no parameter override and no dated output. `backtest.yml` (branch
`claude/project-thread-oks22m-backtest`) runs one replay and `hermes certify`
only. `uv run hermes --help` lists the new command as `funnel  Evaluate the
ADR-021 §5 backtest funnel against a signed pre-registration.`

1. **Driver** `scripts/backtest/run_portfolio_backtest.py`:
   - `--cost-multiplier X` scaling fees and slippage, logged on the trial line;
   - `--funnel-out FILE` writing the Design §2 file: `start_date`, the dated
     daily equity, the trade count, and the trial and neighbour return series;
   - a pre-registered neighbour grid: each neighbour run is logged as a trial
     (ADR-021 §5 "ablations are logged as trials"), and its daily returns go
     into `trials` and `neighbour_trial_ids`;
   - the running strategies' Train + Validation replays, for `running_strategies`.
2. **Workflow** `backtest.yml`, after the Backtest step:
   - a second replay with `--cost-multiplier` equal to the pre-registration's
     `cost_stress_multiplier`, its equity merged into `cost_stress`;
   - a step `uv run hermes funnel "$strategy" --pre-registration
     "docs/experiments/$EXPERIMENT/pre-registration-<v>.md" --evidence
     "$out/funnel-evidence.json" --expect-sha256 "$PRE_REG_SHA256" >
     "$out/funnel.txt"`, with the same exit-code handling as certify (1 is a
     valid outcome, anything else fails);
   - a new input or env value naming the pre-registration file, and its
     signed SHA-256 (`PRE_REG_SHA256`, taken from the lab notebook entry, never
     computed from the checked-out file), which `--expect-sha256` requires;
   - the Summary awk extended to `funnel.txt` and to the `FUNNEL PASSED` / `FUNNEL FAILED` footer:
     `awk '/^(PASS|FAIL) /{print $1, $2; next} /^(# |GATE |FUNNEL |REFUSED|REVERTED)/{print}'`.
   - `funnel-evidence.json` and `funnel.txt` in the uploaded artifact. Neither
     goes into the job summary beyond PASS/FAIL (ADR-023 §7).
3. Each neighbour and stress replay is a trial line, so the run's trial count
   grows by 1 + neighbours + 1 and the DSR deflates accordingly.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`,
   `uv run lint-imports` clean.
3. Synthetic data only: no test reads `/srv`, `HERMES_DATA_DIR` or any kline.

Test tiers touched: unit, property

## Risks

- **Evidence chooses its own neighbours.** `neighbour_trial_ids` comes from the
  evidence, so a producer could omit a bad neighbour. Mitigation: the driver
  derives it from the pre-registered grid and logs every neighbour as a trial;
  the trial log shows any missing one. Deriving the ids from the pre-registration
  is a follow-up.
- **CSCV cost grows as C(S, S/2).** S = 16 gives 12,870 halves; S = 20 gives
  184,756. The parser caps `cscv_blocks` at 20.
- **The base run is not tied to a trial column.** PBO uses the trial matrix,
  the other checks use the base run. If the producer leaves the selected
  configuration out of `trials`, PBO describes a different family. Mitigation:
  the driver writes both from the same replay.
- **Front matter is free YAML.** `yaml.safe_load` only; dates may arrive as
  `date` or ISO strings, both accepted, anything else refused.

## Dependencies on other changes

- ADR-021 §5 (Proposed), ADR-023 §6–§7 (Proposed, branch
  `claude/project-thread-oks22m-backtest`).
- Workflow and driver follow-ups in Design §5.
