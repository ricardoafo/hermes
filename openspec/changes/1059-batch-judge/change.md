# Change: 1059-batch-judge

**SDD change**: `1059-batch-judge`
**Date**: 2026-10-17
**Status**: Applied (parts 1 and 2)
**Tracking issue**: see sibling `issue.md` (#1059)

Mirror of engram topic key `sdd/1059-batch-judge/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-026 (PR #1055) judges a research batch once every candidate's runs are in
the trial log, and picks the champion by a rule signed with the batch. This
change builds that judgement in two parts. Part 1 (this PR) is the pure core:
the Benjamini-Hochberg step, the batch-wide trial count N, and the selection
over a typed list of candidate results. Part 2 wires it into
`hermes funnel --batch` once the generic gate API of issue #1058 exists, and
writes the verdict records of the research funnel interface contract §4-§5.
Main risk: a selection that depends on run order or on float rounding at a
boundary would pick the wrong champion; both are pinned by tests.

## Scope

### In (part 1)

- `src/hermes/domain/statistics/false_discovery.py`: `benjamini_hochberg`.
- `src/hermes/application/calibration/batch_judge.py`: `SelectionRule`,
  `CandidateResult`, `BatchJudgement`, `batch_trial_summary`, `judge_batch`.
- Unit and property tests for both, and `changelog.d/1059-added.md`.

### In (part 2)

- `src/hermes/application/calibration/batch_stage.py`: `judge_stage` runs
  `judge_candidate` (issue #1058) over each candidate's evidence folder with
  one `batch_trial_summary`, then applies `judge_batch` after Validation;
  `verdict_record` and `stage_summary` build the contract §4-§5 files.
- `src/hermes/entrypoints/funnel_batch.py`: `hermes funnel --batch FILE
  --stage train|validation --evidence-dir DIR --trial-log FILE --out DIR`,
  routed from `hermes funnel` when `--batch` is given.
- `tests/support/candidate_evidence.py`: `write_candidate` takes the
  candidate's strategy id, so a test can write both candidates.
- Unit tests for both modules and `changelog.d/+funnel-batch-cli.added.md`.

### Out

- **An implied-independent-trials estimator.** The trial log keeps one Sharpe
  per trial and no return series, so N is the raw count (D-2).

## Decisions

### D-1: Benjamini-Hochberg runs over every candidate in the batch

- **Decision**: m is the batch size. A candidate with no DSR enters with
  p = 1. The survivors are the eligible candidates BH rejects.
- **Alternatives**: m = the eligible count, as ADR-026 §5.2 reads ("among the
  eligible"). Rejected: eligibility already needs DSR >= 0.95, so every
  eligible p = 1 - DSR is <= 0.05 = q, and the largest one sits under the
  line q * m / m. BH could then never remove anyone.
- **Rationale**: false-discovery control is over every hypothesis tested; the
  batch signed and ran all of them. Raised with the Backtesting thread (the
  ADR-026 design authority), which adopted it in the interface contract v2;
  the ADR text fix is pending on PR #1055.
- **Cost of reversal**: LOW (one line and its tests).

### D-2: N is the raw count of every line in the experiment's trial log

- **Decision**: `batch_trial_summary` is `summarise_trials` over the whole
  log with no strategy filter: every trial, every strategy and split, v1 and
  v2 included, and the Sharpe variance over the same lines (interface
  contract v4 §4).
- **Alternatives**: an implied number of independent trials from return
  correlations (pre-registration v2 §8.1 names it): the log has no return
  series to correlate.
- **Rationale**: the raw count bounds any implied N from above, so the DSR it
  gives is the conservative one. The estimator lives in one function.
- **Cost of reversal**: LOW.

### D-3: Boundary comparisons use exact decimals

- **Decision**: DSRs and q are read as the decimals they print as
  (`Fraction(repr(x))`) and BH compares exact fractions.
- **Alternatives**: floats. In floats 1 - 0.95 = 0.05000000000000004 > 0.05,
  so a candidate exactly on its line would fail; the test
  `test_a_p_value_exactly_on_the_bh_line_survives` failed this way first.
- **Cost of reversal**: LOW.

### D-4: The DSR floor stays a gate row

- **Decision**: eligible = the gate passed (DSR row included) and a DSR
  exists. `SelectionRule` holds only `fdr_q`.
- **Rationale**: the interface contract §3 puts `dsr` among the gate's checks,
  thresholds from the batch's `backtest_funnel`; a second floor here could
  disagree with it.
- **Cost of reversal**: LOW.

### D-5: Ranking and ties

- **Decision**: survivors rank by DSR (highest first), then by lowest mean
  Pearson correlation of daily returns with the other survivors, then by
  strategy id. A correlation that is undefined (a flat series) counts as 1.
- **Rationale**: ADR-026 §5.3; the id makes the order total, so it never
  depends on run order. An undefined correlation ranks last (fails closed).
- **Cost of reversal**: LOW.

### D-6: Every candidate gets a verdict at every stage

- **Decision**: at both stages the judge walks every candidate the batch
  names. A candidate with no `DIR/<strategy_id>/` gets one check,
  `missing_evidence` FAIL (detail `no Train evidence` or `no Validation
  evidence`, in `funnel-batch.txt` only), a `null` window and `passed: false`. At Validation
  it enters BH with p = 1 (D-1).
- **Alternatives**: judge only the folders present. Rejected: BH's m must be
  the batch size, and a candidate whose Validation job died must show as a
  failure, not vanish. A candidate that failed Train also gets a
  `missing_evidence` Validation record; the Train record says why.
- **Cost of reversal**: LOW.

### D-7: The tie-break's returns come from the funnel evidence

- **Decision**: a candidate's daily returns are those of
  `funnel-evidence.json`'s `daily_equity`, and its window is that file's first
  and last day. Every judged candidate must cover the same window, or the run
  stops with exit 3. A candidate without evidence carries zeros of the common
  length; it is never eligible, so they are never read.
- **Rationale**: the funnel evidence is the file the gate's funnel rows read,
  with explicit dates; the performance file holds the same curve without them.
- **Cost of reversal**: LOW.

### D-8: Refuse instead of guessing

- **Decision**: a DRAFT batch is REFUSED (exit 1, nothing written), as
  `hermes certify --batch` does. A missing trial log is exit 3: an empty log
  would judge every candidate with N = 0. Unreadable evidence, evidence of the
  wrong stage and a broken log chain are exit 3, and no output is written.
- **Cost of reversal**: LOW.

### D-9: stdout carries statuses only

- **Decision**: one `PASS|FAIL|NA <strategy_id> <check>` line per check, then
  `CANDIDATES <ids>` or `CANDIDATES none` (Train), or `CHAMPION <id>` or
  `CHAMPION none` (Validation). Values go only to `funnel-batch.txt`
  (ADR-023 §7, ADR-012 D-06). `NA` lines are printed because the workflow
  summary keeps them (contract §3).
- **Cost of reversal**: LOW.

## Requirements

### R-1: BH follows the step-up rule

- **A-1.1**: `uv run pytest tests/unit/domain/statistics/test_false_discovery.py`
  passes: hand-computed cases, a p-value on and just over its line, ties,
  refusals, and properties (order invariance, rejected set is the smallest
  p-values, every rejection backed by a rank under its line, no larger rank
  under its line).

### R-2: The judgement follows the signed rule and ignores run order

- **A-2.1**: `uv run pytest tests/unit/application/calibration/test_batch_judge.py`
  passes, including the property that any permutation of the candidates gives
  the same judgement.

### R-3: The stage outputs follow the contract

- **A-3.1**: `uv run pytest tests/unit/application/calibration/test_batch_stage.py`
  passes: who goes on after Train, the champion and BH over the whole batch
  after Validation, N over the whole log for every candidate, `trial_lines`
  per candidate, batch and split, and a verdict record whose only number is
  `trial_lines` (`run_id` until the workflow fills it).

### R-4: The CLI's exit codes and lines follow the contract

- **A-4.1**: `uv run pytest tests/unit/entrypoints/test_funnel_batch.py`
  passes: exit 0/1/2/3 cases, stdout lines with no values, the files written,
  and the single pre-registration path unchanged.

## Design

```python
@dataclass(frozen=True, slots=True)
class SelectionRule:
    fdr_q: float  # 0 < q < 1


@dataclass(frozen=True, slots=True)
class CandidateResult:
    strategy_id: str
    gate_passed: bool  # GateResult.passed (#1058)
    dsr: float | None  # GateResult.dsr, judged with N
    daily_returns: tuple[float, ...]  # for the tie-break


@dataclass(frozen=True, slots=True)
class BatchJudgement:
    eligible: tuple[str, ...]  # rule order; after Train, these go on
    survivors: tuple[str, ...]  # rule order
    champion: str | None  # property: first survivor


def batch_trial_summary(records: Iterable[TrialRecord]) -> TrialSummary: ...
def judge_batch(candidates: Sequence[CandidateResult], rule: SelectionRule) -> BatchJudgement: ...
def benjamini_hochberg(p_values: Sequence[Fraction], *, q: Fraction) -> tuple[bool, ...]: ...
```

`judge_batch` refuses an empty batch, a repeated strategy id and return
series of different lengths.

## Test plan

1. Part 1: tests written before the code; the first run failed on the missing
   modules, then on the float boundary (D-3). Part 2: tests written after the
   modules, from contract §4-§5, with every expected file worked by hand.
2. `bash scripts/dev/mutation-diff.sh` on the new lines.
3. `GITHUB_BASE_REF=main bash scripts/dev/ci-checks.sh guards`, ruff, mypy.

Test tiers touched: unit, property, mutation

## Risks

- **The ADR text and D-1 differ.** If the Backtesting thread keeps
  m = eligible, BH becomes a no-op; the change is one line and the tests say
  which.
- **Part 2 maps the gate's result.** A `GateResult.passed` that ignored the
  DSR row would let a low-DSR candidate be eligible; `test_signed_gate.py`
  pins that a failed DSR row fails the gate, and `judge_batch` also requires
  a DSR.

## Dependencies on other changes

- PR #1055 (ADR-026) and issue #1054.
- Part 2: issue #1058 (`judge_candidate`, `GateResult`) and issue #1057
  (`load_batch`).
