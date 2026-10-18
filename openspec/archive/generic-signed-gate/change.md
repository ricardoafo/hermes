# Change: generic-signed-gate

**SDD change**: `generic-signed-gate`
**Date**: 2026-10-18
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#1058)

## Summary

One backtest gate for any strategy signed with a funnel block: pre-registration
v2 and every candidate of an ADR-026 research batch. `evaluate_signed_gate` is
v2's gate with its thresholds as arguments; v2 now calls it, so its output is
unchanged. `judge_candidate` judges a batch candidate from its three evidence
files with the thresholds the batch signed and the trial summary the batch
judge passes in. On a Train-only run `validation_vs_train` is NA (issue #1037).
`hermes certify <id> --batch FILE` prints the verdict and records
`BACKTEST_CERTIFIED` after a Train + Validation pass. Main risk: a change in
v2's verdict; v2's gate tests run unchanged, and one test compares v2's report
with the generic gate's on the same input.

## Scope

### In

- `application/calibration/signed_gate.py`: `evaluate_signed_gate`,
  `judge_candidate`, `candidate_gate_evidence`, `require_v1_secondary`,
  `evidence_split`, `check_result`.
- `application/calibration/evidence_files.py`: the evidence, performance and
  funnel evidence readers, moved unchanged out of `entrypoints/certify.py` and
  `entrypoints/funnel.py` so the application layer can read a candidate's
  files; adds `load_performance`.
- `application/calibration/certification_gates_v2.py`: `evaluate_v2_gate`
  delegates to `evaluate_signed_gate`.
- `application/calibration/research_batch.py`: `ResearchBatch.body` and
  `section_text`, so the gate can read a candidate's section; the
  `content_sha256` check also refuses a batch edited after signing.
- `entrypoints/certify.py`: `--batch FILE` and `--trial-log FILE`.

### Out

- The batch judge, selection and verdict records (issue #1059).
- Gates past BACKTEST_CERTIFIED for batch candidates: ADR-026 signs the
  backtest gate only, so they are refused.

## Decisions

### D-1: v2 runs through the generic gate

Keeping two copies of the same rows would let them drift. v2's module keeps its
rule parser and passes its DSR floor and funnel block in.

### D-2: §9 secondary thresholds are v1's, and the section must say so

A batch's front matter signs no §9 numbers. Each candidate's section is written
from pre-registration v2, whose §9 says the secondaries are "Unchanged from v1".
The gate requires that sentence under a `Secondary` heading inside the
candidate's `9.` heading and then uses v1's thresholds, as v2 does; a section
that says anything else is refused, not judged against numbers it did not sign.

### D-3: The split comes from the evidence

`judge_candidate` takes the split from its caller and refuses evidence that
does not match it: a run that ends on or before `train_end` is Train only, any
later end is Train + Validation. `certify --batch` reads the split from the
evidence the same way, so a Train run cannot be judged as Validation and a
Validation run cannot hide a check behind NA.

### D-4: DSR uses the trial summary passed in

The DSR is computed from the performance file's returns with the caller's
`TrialSummary`, not with the run's own `trials` block, so every candidate in a
batch is deflated by the same N (ADR-026 §4). No Sharpe variance means no DSR,
and the row fails closed.

### D-5: A Train pass records nothing

The gate out of UNTRUSTED is judged on Train + Validation. `certify --batch` on
Train evidence prints the verdict and exits 0 or 1 without touching the store.

### D-6: A batch edited after signing fails `content_sha256`

`content_sha256` proves what the precursor held, but the file checked could
have moved on from it (review finding on PR #1073). The check now also
requires the batch to equal its precursor apart from the signature keys
(`status`, `signed_by`, `date_signed`, `content_sha256`,
`content_sha256_scope`, `signing_commit_precursor`, `code_commit`), so the
signature covers exactly the rules that run. This merges before the first
batch is sent for signing.

## Requirements

- R-1: `validation_vs_train` is NA on a Train-only run and binding on Train +
  Validation.
- R-2: Every threshold comes from the batch's `backtest_funnel` block and the
  candidate's section.
- R-3: The DSR uses the trial summary passed in.
- R-4: An id the signed batch does not name is refused; a DRAFT batch is refused.
- R-5: v1 and v2 outputs and records are unchanged.
- R-6: A new candidate certifies with no code beyond its roster entry.
- R-7: A batch whose rules or body changed after signing fails
  `hermes batch check --require-signed`.

## Design

`judge_candidate` reads the files with `candidate_gate_evidence`, checks the
split, runs `evaluate_signed_gate` on a fresh UNTRUSTED record and maps each
row to PASS, FAIL or NA. `certify --batch` loads the batch from its
repo-relative path, summarises the trial log (default: the batch's experiment
log), and for Train + Validation goes through `run` with the generic gate, so
recording, reverts and the evidence hash work as for v1 and v2. The parameter
fingerprint covers the batch id, the candidate's signed base values, its
capital budget and the universe.

## Test plan

- Unit: NA on Train, binding on Validation, every row PASS on strong evidence,
  DSR deflated by N, no variance fails the row, refusals (unknown id, split
  mismatch, OOS window, changed secondaries), the §9 sentence parser, the
  evidence hash chain, v2 and generic reports equal; a parameter or body
  edited after signing fails `content_sha256`, the signing keys alone pass.
- CLI: a committed edit to a signed batch fails `batch check --require-signed`;
  Train prints NA and records nothing; Validation records
  BACKTEST_CERTIFIED with only a roster entry added; usage and refusal exit codes.
- Existing v1 and v2 certify, funnel and gate tests run unchanged.

## Risks

- A candidate that needs other §9 numbers needs a batch format change first.
