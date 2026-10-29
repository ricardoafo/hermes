---
batch_id: <b1>
experiment: <experiment>
status: DRAFT
signed_by: null
date_signed: null
content_sha256: null
content_sha256_scope: null
signing_commit_precursor: null
code_commit: null
selection:
  eligible: full_gate_train_validation
  fdr: benjamini_hochberg
  fdr_q: 0.05
  rank: dsr
  tie_break: lowest_mean_return_correlation
backtest_funnel:
  dsr_min: 0.95
  train_end: 2024-06-30
  oos_start: 2025-09-01
  cscv_blocks: 16
  pbo_max: 0.05
  t_stat_above: 3.0
  trade_count_min: 100
  cost_stress_multiplier: 2.0
  positive_year_share_above: 0.5
  validation_to_train_return_min: 0.5
  neighbour_sharpe_ratio_min: 0.5
  return_correlation_below: 0.7
  regimes:
    - {kind: bull, start: 2020-10-01, end: 2021-11-10}
    - {kind: bear, start: 2021-11-11, end: 2022-11-21}
    - {kind: bull, start: 2022-11-22, end: 2024-03-14}
candidates:
  - strategy_id: <strategy-id-1>
    idea_family: <family-1>
    card: docs/experiments/<experiment>/cards/<strategy-id-1>.md
    card_sha256: <sha256 of the card file>
    section: C1
    summary: |
      <One to six plain lines on the idea, its mechanism and its expected sign.>
    code_paths:
      - <src/hermes/... signal module>
      - <tests/... its tests>
    parameters:
      <parameter_name>: {base: 20, neighbours: [15, 25]}
  - strategy_id: <strategy-id-2>
    idea_family: <family-2>
    card: docs/experiments/<experiment>/cards/<strategy-id-2>.md
    card_sha256: <sha256 of the card file>
    section: C2
    summary: |
      <One to six plain lines.>
    code_paths:
      - <src/hermes/... signal module>
    parameters:
      <parameter_name>: {base: "3.0", neighbours: ["2.5", "3.5"]}
---

# Research batch <b1>: <experiment>

<!--
A research batch (ADR-026 §1). Copy it to
docs/experiments/<experiment>/batches/<batch_id>.md. It holds 2 to 6
candidates, one per idea family. Each candidate's card is committed first, and
its code, strategy id, config entry and tests are merged before the batch is
signed. Integers stay integers; decimals are quoted strings. Check it with
`uv run hermes batch check <file>`.

Signing, as pre-registration v2: commit the finished DRAFT (the precursor),
then in the signing commit set `status: SIGNED`, `signed_by`, `date_signed`,
`signing_commit_precursor: <precursor sha>`,
`content_sha256: <git show <precursor>:<path> | sha256sum>`,
`content_sha256_scope: git show <precursor>:<path> | sha256sum` and
`code_commit: <the main commit the code is bound to>`. The Code Owner signs by
approving and merging the batch's PR. A signed batch is never edited; a
correction is a new batch.
-->

## Selection

The champion rule of ADR-026 §5, fixed by the `selection` block before any run: eligible are the candidates that pass
the full gate on Train + Validation; Benjamini-Hochberg at q = `fdr_q` over every tested candidate, with 1 - DSR as
the p-value; the champion is the survivor with the highest DSR, and a tie goes to the lowest mean daily-return
correlation with the other survivors. No survivor: no champion, and one post-mortem covers the batch within 7 days.

## C1 — <strategy-id-1>

<A full pre-registration body written from pre-registration-v2.md: hypothesis, universe, windows, entry, exit,
sizing, fees, kill rules, shadow length.>

## C2 — <strategy-id-2>

<As C1.>
