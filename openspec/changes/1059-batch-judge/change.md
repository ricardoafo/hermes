# Change: 1059-batch-judge

**SDD change**: `1059-batch-judge`
**Date**: 2026-10-17
**Status**: Applied (part 1 of 2)
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

### Out

- **Part 2, `hermes funnel --batch --stage train|validation`.** Needs
  `judge_candidate` and `GateResult` from issue #1058 (Defects lane) and
  `load_batch` from issue #1057. It maps each `GateResult` to a
  `CandidateResult` (`passed`, `dsr`, the daily returns from
  `performance.json`), calls `judge_batch`, and writes
  `validation-candidates.json`, `selection.json`, `verdicts/<id>.json` and
  `funnel-batch.txt` to the contract's paths, with no value on stdout.
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

## Design

```python
@dataclass(frozen=True, slots=True)
class SelectionRule:
    fdr_q: float                      # 0 < q < 1

@dataclass(frozen=True, slots=True)
class CandidateResult:
    strategy_id: str
    gate_passed: bool                 # GateResult.passed (#1058)
    dsr: float | None                 # GateResult.dsr, judged with N
    daily_returns: tuple[float, ...]  # for the tie-break

@dataclass(frozen=True, slots=True)
class BatchJudgement:
    eligible: tuple[str, ...]         # rule order; after Train, these go on
    survivors: tuple[str, ...]        # rule order
    champion: str | None              # property: first survivor

def batch_trial_summary(records: Iterable[TrialRecord]) -> TrialSummary: ...
def judge_batch(candidates: Sequence[CandidateResult], rule: SelectionRule) -> BatchJudgement: ...
def benjamini_hochberg(p_values: Sequence[Fraction], *, q: Fraction) -> tuple[bool, ...]: ...
```

`judge_batch` refuses an empty batch, a repeated strategy id and return
series of different lengths.

## Test plan

1. Tests written before the code; the first run failed on the missing
   modules, then on the float boundary (D-3).
2. `bash scripts/dev/mutation-diff.sh` on the new lines.
3. `GITHUB_BASE_REF=main bash scripts/dev/ci-checks.sh guards`, ruff, mypy.

Test tiers touched: unit, property, mutation

## Risks

- **The ADR text and D-1 differ.** If the Backtesting thread keeps
  m = eligible, BH becomes a no-op; the change is one line and the tests say
  which.
- **Part 2 maps the gate's result.** A `GateResult.passed` that ignored the
  DSR row would let a low-DSR candidate be eligible; part 2's tests pin that
  `dsr` is among the checks.

## Dependencies on other changes

- PR #1055 (ADR-026) and issue #1054.
- Part 2: issue #1058 (`judge_candidate`, `GateResult`) and issue #1057
  (`load_batch`).
