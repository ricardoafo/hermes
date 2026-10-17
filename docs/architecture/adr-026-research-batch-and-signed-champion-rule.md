# ADR-026: Research batches — several signed candidates backtested together, Validation started by the pipeline, and a champion picked by a signed rule

**Date**: 2026-10-17
**Status**: Proposed — Accepted when the Code Owner merges it
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: in [ADR-023](adr-023-backtest-runs-on-server-01.md), §5 (a Validation run waits for the Code Owner's
own click) and §9 (`workflow_dispatch` is the only trigger), for strategies signed in a batch.
**Amends**: [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) §1 stages 1, 3 and 4 (one signature covers a
batch; the gate is judged batch-wide) and §5 (how shadow candidates are chosen); [ADR-023](adr-023-backtest-runs-on-server-01.md)
§6 (a workflow job, not an AI session, opens the PR that commits trial lines). Pulls the backtest half of issue #967
forward to MVP1.

---

## Context

v1 failed its backtest gate on 2026-10-08 and v2 failed on Train on 2026-10-17. Each took one pre-registration, one
signature, and a manual click at every step: dispatch Train, read the verdict, dispatch Validation from the Code
Owner's account, commit the trial lines by hand. With one strategy at a time and a gate that rejects most ideas, the
first shadow is far away. On 2026-10-17 the Code Owner asked for a research funnel instead: research several
strategies, backtest them in one batch without him running steps, evaluate them, and pick a champion for the first
shadow, with several shadows later (issue #967).

What the repo does today (checked on main at 95d2786):

- `backtest.yml` runs one hard-coded strategy (`STRATEGY: swing-mvp1-v2`, `PRE_REGISTRATION: …-v2.md`), by
  `workflow_dispatch` only, one run at a time (`concurrency: backtest`).
- `hermes certify` has a gate module per strategy version (`certification_gates_v2.py`). A new strategy needs new
  gate code.
- Signal parameters (lookbacks, thresholds) live in code. `config/strategies.yaml` holds name, capital, sizing and
  exit pricing.
- Trial lines leave as an artifact, and a follow-up PR commits them (ADR-023 §6). On 2026-10-17 the AI session
  that committed v2's lines had to download the artifact to do it, which ADR-023 §7 forbids.

What binds and stays:

- **Every trial counts** (ADR-012 D-11, ADR-021 §5): from v2 on, every trial on a dataset counts toward DSR across
  versions. A batch of candidates raises the bar for each of them.
- **The AI sees PASS/FAIL only** (ADR-012 D-06, ADR-023 §7) until v1 OOS certification, not before 2027-01-01. So the
  champion cannot be picked by an agent's judgement. It must come from a rule signed before any run, applied in code.
- **Pre-registration binds.** A candidate's rules, parameters, universe, sizing, fees, kill rules and thresholds are
  frozen when signed. Nothing is tuned after a run, and a changed candidate is a new variant at stage 1.
- **The sealed window stays sealed.** No run reads data from 2025-09-01 on before certification (ADR-023 §4).

The Code Owner's answers on issue #967 (2026-10-17) set the direction this ADR follows:

1. The pipeline starts Validation; signing the pre-registration is the OK.
2. A shadow starts by itself once shadow Ready holds.
3. The parameter chat works before launch only, never on a running test.
4. The chat writes drafts that he signs.
5. A new console screen holds it.
6. Only drafts he signs count as trials.

And for the shadow line: up to 6 shadows plus the champion, at most one per idea family, the cap a signed setting,
and strategies that pass wait in order without displacing a running shadow.

## Decision

### 1. A research batch is one signed document with several candidates

A batch lives at `docs/experiments/<experiment>/batches/<batch-id>.md`. Its front matter holds:

- `batch_id`, `status: SIGNED`, `signed_by`, `content_sha256` and its scope, as pre-registrations do today;
- `candidates`: 2 to 6 entries, each with `strategy_id`, `idea_family`, `card_sha256`, the section that holds its
  rules, and a `summary`: a few plain lines on the idea, its mechanism and its expected sign, which the console's
  Lab screen shows (issue #1056);
- `selection`: the champion rule of §5, with its numbers;
- `backtest_funnel`: the gate thresholds, the same block pre-registration v2 introduced. One block binds every
  candidate in the batch; a batch cannot loosen the ADR-021 §5 floors (DSR ≥ 0.95, PBO ≤ 0.05, t > 3, ≥ 100 trades).

Each candidate's section is a full pre-registration body (hypothesis, universe, windows, entry, exit, sizing, fees,
kill rules, shadow length), written from the v2 template. Each candidate also has a hypothesis card (ADR-021 stage 1:
mechanism, expected sign, free-parameter cap, falsification test), committed and hashed before the batch is signed.

**Signing.** The Code Owner signs the batch by approving and merging its PR, as with pre-registrations today. One
signature signs every candidate in it. A batch is immutable once signed: a correction is a new batch, and the
candidates of the old one keep their trials.

**One candidate per idea family per batch.** Two variants of one idea move together, add trials and add little
information. A family is named by its entry signal: a candidate that changes only sizing, filters or exits on an
existing entry signal is in that signal's family. v1 and v2 are in the 4h Donchian breakout family.

**Code before signing.** A candidate's signal code, strategy id, config entry and tests merge before the batch is
signed, through normal PRs that never run a backtest. The batch names the commit it binds; the pipeline refuses to
run a candidate whose code differs from that commit's.

### 2. Train runs for every candidate on its own

When a signed batch lands on main, `backtest.yml` starts by itself (`push` to main on `docs/experiments/*/batches/**`).
It proceeds only when:

- the batch's `content_sha256` matches its file, and its signing commit's merge was made by the Code Owner;
- every candidate's card hash matches its committed card;
- every candidate's code is the commit the batch names.

It then runs each candidate's Train funnel touch in batch order, one job after the other on the `hermes-backtest`
runner (ADR-023 §1 and §8 unchanged: one runner, never concurrent, 120 minutes per job). Each job appends that
candidate's trial lines to the log the previous job produced, so the hash chain stays linear. A candidate that errors
(not a FAIL verdict, an error) stops the batch; the trial lines already appended still count.

### 3. Validation is started by the pipeline, only where Train passed

Following answer 1 on issue #967, the signed batch is the Code Owner's OK for its Validation touches. This replaces
ADR-023 §5's own-account click for batch candidates. Single pre-registrations outside a batch keep §5 as written.

- After every candidate's Train touch is done, the batch's Train results are judged together (§4). Only candidates
  that pass every Train-judgeable check go on to Validation. A failed candidate never spends a touch.
- Validation runs in the `backtest-validation` environment, from main only. Instead of checking that the Code Owner
  started the run, the job checks that the run belongs to a signed batch whose merge he made (§2), and that the
  candidate passed Train in this batch's run.
- Each Validation run is one touch for the candidate's family. The job counts the family's touches from the trial
  log (lines with `split: validation`) and refuses a run past the ADR-021 §5 budget of 5.
  v1's Validation touches count against its family too: the budget starts from v2 in ADR-021 §5, but counting
  them is the conservative reading.
- Agents still never dispatch a Validation run. A rerun of a Validation job is refused unless the first attempt died
  before any data was read.

### 4. DSR and trial accounting across a batch

- Every Train and Validation run of every candidate appends its trials to the same experiment trial log, before any
  metric is released (ADR-012 D-11). v1's and v2's trials stay in it.
- **The batch is judged at its end, not candidate by candidate.** Each candidate's DSR uses N, the implied
  independent trials across every trial in the log once the whole batch's Train touches are appended. Judging in run
  order would give the first candidate a smaller N than the last; judging at the end gives every candidate the same
  N. The same holds after Validation.
- Only signed candidates count (answer 6 on issue #967). A draft that is never signed runs nothing and logs nothing.
  Drafts that are discarded are listed in the batch's PR for the record, without trial lines.
- Ablations and reruns are trials too. A rerun of a job that died before reading data logs nothing.

### 5. The champion comes from a rule signed with the batch

The `selection` block fixes, before any run:

1. **Eligible**: candidates that pass the full gate on Train + Validation: every §9 metric, DSR ≥ 0.95 with the
   batch-wide N, and every funnel check, as `hermes certify` computes them.
2. **False-discovery control**: among the eligible, keep those that survive Benjamini–Hochberg at the batch's signed
   q (default 0.05), using 1 − DSR as each candidate's p-value (ADR-021 §5, "combining results").
3. **Champion**: the survivor with the highest DSR. A tie goes to the lowest mean daily-return correlation with the
   other survivors.
4. **None**: if no candidate survives, the batch has no champion, nothing goes to shadow, and the batch writes one
   post-mortem that covers every candidate, within 7 days.

A pipeline step prints the result as PASS/FAIL per check per candidate and the champion's strategy id, or `none`. The
id and the order of survivors are ordinal, not outcome values; they are the only selection output an agent sees.
Recording `BACKTEST_CERTIFIED` for the champion stays a Code Owner step on the VPS (ADR-023 §6).

Other survivors are shadow candidates: they wait in the shadow line (§7) in the order of the rule.

### 6. Results leave the runner without an AI session handling them

The backtest jobs keep `contents: read` and no write token (ADR-023 §6, ADR-019). After the batch finishes, a separate
job with a narrowly scoped token takes the trial lines and the PASS/FAIL summary from the batch's artifacts and opens a
bot PR that:

- appends the trial lines byte for byte, in order;
- appends one lab-notebook entry per candidate with PASS/FAIL per check and no values;
- commits one verdict record per candidate at `docs/experiments/<experiment>/verdicts/<batch-id>/<strategy-id>.json`,
  next to the trial lines: the batch id, the run, the commit, the split, and PASS, FAIL or not applicable for every
  certify and funnel check, with no metric value. The console's Lab screen reads these (issue #1056);
- records the champion's id or `none`.

No agent downloads the artifact. The values stay in it for the Code Owner (ADR-023 §7). The exact token and its
scope are decided in the build issue, under ADR-019.

### 7. Shadow

- The first champion enters shadow through shadow Ready (ADR-021 §2) and `hermes shadow-start` (issue #797), which
  the Code Owner runs on the VPS until the MVP2 pipeline lands.
- Per answer 2 on issue #967, a shadow starts by itself once Ready holds when that pipeline lands (issue #967 build),
  with the shadow-line rules recorded there: up to 6 shadows plus the champion, at most one per idea family, the cap
  a signed setting, and passing strategies waiting in order without displacing a running shadow.
- Shadow length, kill rules and drift checks come from each candidate's own section; ADR-021 §2 and §5 bounds apply.

### 8. Where the AI fits

- Agents draft hypothesis cards and batch documents from the literature, the lessons register (ADR-021 §6) and
  PASS/FAIL records. They never see a value, and they never write to a signed batch.
- The console screen and parameter chat of issue #967 (answers 3 to 5) produce drafts for the next batch. They stay
  MVP2 and are not decided further here.
- Gate and selection decisions are computed in code (ADR-012 D-08). No agent decides pass, fail or champion.

## Security (ADR-019)

- Train and Validation run from main only, on the one backtest runner, as today.
- The automatic trigger trusts only a batch whose merge the Code Owner made, whose hash matches, and whose code is
  the commit it names. A bot push, a PR branch or an edited file starts nothing.
- The job that opens the results PR has its own token, scoped to that PR and nothing else. The backtest jobs hold
  none.

## Why

- **A batch spends trials knowingly.** The trial budget is fixed by the data, about 45 independent configurations in
  roughly 5 years (lab scan of 2026-10-06). Signing a batch decides up front how much of it to spend.
- **Judging the batch at its end** makes every candidate face the same N, so run order cannot help or hurt one.
- **A signed selection rule** is the only way to pick a champion while the AI stays blind and the Code Owner does not
  pick by eye.
- **Validation only after a Train pass** keeps the family budget for candidates that could pass.

Alternatives considered:

- **Keep one pre-registration and one click per strategy.** Rejected; that is the process that took v1 and v2 a month
  each.
- **Let the pipeline pick the champion by best Train result.** Rejected; that selects the luckiest backtest
  (overfitting by selection) and ignores Validation.
- **A grid search over parameters.** Rejected; brute-force grids burn the trial budget (ADR-021 §5, issue #361).
- **One click from the Code Owner per batch before Validation.** Rejected by the Code Owner's answer 1 on issue #967;
  the signature is the OK.

## Consequences

### Positive

- One signature starts a whole batch; the Code Owner's next step is the champion on the VPS.
- Every candidate faces the same deflation, the same gate and a rule fixed before any run.
- Trial lines and PASS/FAIL records land in the repo with no AI session touching the values.

### Negative

- Each batch adds trials, so later batches face a higher bar. A batch should be small and diverse.
- New strategies still need signal code and tests before signing; the batch removes clicks, not engineering.
- More workflow surface: an automatic trigger, a batch manifest, a results job with a write token.

### Neutral

- Pre-registrations v1 and v2, their windows and their thresholds are unchanged. A single pre-registration still runs
  under ADR-023 as written.
- The OOS window stays sealed until certification.

## What would change this decision

- Evidence that batch-wide N or the BH step rejects sound strategies or passes overfit ones. The numbers change by
  decision record; the structure by a new ADR.
- The MVP2 lab ADR for issue #967, which may supersede §7 and §8 when the console screen and parallel shadows land.

## Cross-references

- [ADR-012](adr-012-continuous-ai-evaluation.md) — D-06 outcome blindness, D-08 enforcement in code, D-11 research
  loop and trial counting
- [ADR-019](adr-019-security-baseline-and-threat-model.md) — tokens and workflow security
- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — stages, shadow Ready and Done, the funnel and the
  Validation budget
- [ADR-023](adr-023-backtest-runs-on-server-01.md) — the backtest runner, data and artifacts
- Issue #967 — the MVP2 lab and the Code Owner's six answers
- Issue #1056 — the console's read-only Lab screen, which reads `summary` and the verdict records
- Issue #1054 — this ADR

## Sources

- Bailey, López de Prado — [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- Bailey, Borwein, López de Prado, Zhu — [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
- Benjamini, Hochberg — Controlling the False Discovery Rate (1995)
