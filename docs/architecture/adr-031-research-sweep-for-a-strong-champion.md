# ADR-031: Research sweeps, 100+ signed candidates per batch to find one strong champion

**Date**: 2026-10-19
**Status**: Accepted (merged in PR #1284)
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: nothing. Extends [ADR-026](adr-026-research-batch-and-signed-champion-rule.md) §1 (batch size and
one candidate per family) and §5 (the Benjamini–Hochberg m) for sweep batches only. Tracking issue: #1280.

---

## Context

Batches swing-b1 and swing-b2 tested four candidates, one per idea family. All four failed Train in swing-b2
(run 39265773532), so there is no champion and no shadow. On 2026-10-19 the Code Owner asked for 100+ strategies on
4h and 1h candles, to find a strong champion for shadow testing, with any downloaded data stored on server-01.

ADR-026 does not allow that as written. A batch holds 2 to 6 candidates, one per idea family, and a grid search is
rejected because it burns the trial budget. That budget is real: about 45 independent configurations in roughly
5 years of 4h data (lab scan of 2026-10-06). Testing 100+ variants and keeping the best one finds luck, not edge,
unless the test charges for every variant tried.

Compute is not the limit. swing-b2's four Train touches took about 3 minutes on server-01, and the pinned data
fills itself there (ADR-023 §2 and §3).

What binds and stays: every trial counts (ADR-012 D-11), the AI sees PASS/FAIL only (ADR-012 D-06, ADR-023 §7),
pre-registration binds, the sealed window from 2025-09-01 stays sealed (ADR-023 §4), and the ADR-021 §5 floors
hold (DSR ≥ 0.95, PBO ≤ 0.05, t > 3, ≥ 100 trades).

## Decision

### 1. A sweep batch is a signed batch with many candidates from signed grids

A sweep batch is an ADR-026 batch with `kind: sweep`. Instead of hand-written candidates it holds:

- `families`: up to 12 idea families. Each names a signal template in code (merged with tests before signing, as
  ADR-026 §1 requires), its hypothesis card and hash, its bar interval (`4h`, or `1h` once §6 allows it), and a
  `grid`: the values each free parameter takes. The free-parameter cap of the card still applies.
- `max_candidates`: the signed total, at most 150. The pipeline expands the grids in a fixed order and refuses a
  batch whose expansion exceeds it.
- `selection` and `backtest_funnel` blocks as ADR-026 §1 and §5 define them, plus `validation_per_family` (§3).

Each grid point is a candidate with a generated id (`<family>@<param>=<value>,...`). Its rules are its family's
template at that point; nothing else differs between candidates of one family. Sizing, fees, universe, exits and
kill rules come from the family's section and are the same for every point.

One signature signs the whole sweep. A sweep is immutable once signed, like any batch.

### 2. Every candidate runs Train, and every trial counts

- The pipeline runs Train for every candidate, in expansion order, in jobs of up to 20 candidates each on the one
  `hermes-backtest` runner. Jobs never run in parallel and keep the 120-minute limit (ADR-023 §1 and §8). Trial
  lines append to one log in order, so the hash chain stays linear.
- Each candidate's DSR uses the batch-wide N of ADR-026 §4, the implied independent trials across the whole log
  once every Train touch of the sweep is in. Grid points of one family are correlated, so N grows slower than the
  candidate count, but it grows. A champion from a sweep has cleared a bar that already charges for every variant.
- A candidate that errors stops the sweep, as in ADR-026 §2.

### 3. Validation only for the strongest Train passer of each family

- After Train, a candidate goes on only if it passes every Train-judgeable check with the batch-wide N.
- Of those, the pipeline sends to Validation at most `validation_per_family` per family (signed, default 1). It
  picks the one with the highest Train DSR, computed and compared in code. No agent sees or ranks the values.
- The ADR-021 §5 budget of 5 Validation touches per family still binds, counted from the trial log as ADR-026 §3
  does. A family past its budget sends nothing.
- At most 12 candidates reach Validation in one sweep, one per family.

### 4. The champion rule for a sweep

The ADR-026 §5 rule applies with one change. Benjamini–Hochberg runs over the candidates sent to Validation, so m
is their count, at most 12. Their p-value is 1 − DSR on Train + Validation with the batch-wide N.

Why not m = every candidate tested: with m = 150, a single survivor would need DSR ≥ 0.99967, a bar almost nothing
real clears. The selection effect of the sweep is already charged in N, which deflates every DSR for all 150 trials.
Charging it again in m would count the same trials twice. The conservative alternative, m = all candidates, is
recorded under "What would change this decision".

The champion is the survivor with the highest DSR, ties broken as in ADR-026 §5. If nothing survives, there is no
champion and one post-mortem covers the sweep.

### 5. Data stays on server-01

Every dataset a sweep needs is downloaded to and read from `/srv/hermes-data` on server-01, pinned in the data
manifest and verified before use (ADR-023 §2 and §3). Nothing is downloaded to the PC, the VPS or a CI runner for a
sweep. The sealed window stays sealed: no dataset reaches 2025-09-01.

### 6. 1h families join when the engine runs 1h everywhere

ADR-029 lets a candidate decide on 1h bars only when backtest, shadow and prod run 1h together. Until issues #1237
(1h market data) and #1238 (replay and parity) land, a sweep may hold 4h families only. After they land, a family may
name `1h`, and the ADR-029 cap of 250 round trips per 90 days per candidate applies.

### 7. Results and shadow

- The results job of ADR-026 §6 commits one verdict record per candidate, PASS/FAIL per check and no values, and the
  champion's id or `none`. The lab notebook gets one entry per family, not per grid point.
- The champion enters shadow through shadow Ready (ADR-021 §2) and `hermes shadow-start`, as ADR-026 §7 says.

### 8. Where the AI fits

Agents draft families, cards and grids from the literature and the PASS/FAIL record (ADR-026 §8). They never see a
value, never rank candidates and never write to a signed sweep. Signals are computed by code; no LLM generates one
(AGENTS.md rule 8).

## Why

- **Volume is cheap; false winners are not.** server-01 runs 100+ candidates in about an hour. What a sweep
  must buy is a champion that stays strong out of sample, so every variant is charged in N.
- **Family templates with grids** make 100+ candidates without 100+ hand-written sections, and keep each family's
  rules identical across its points.
- **One Validation touch per family** protects the family budget and keeps Validation honest: it sees few
  candidates, each the best its family offered on Train.

Alternatives considered:

- **Run ten 6-candidate batches.** Same trial count, ten signatures, and BH over 6 each time. Rejected as slower
  with no gain in rigour.
- **An uncounted exploration sweep, then a small counted batch.** Rejected: the second batch would test ideas picked
  on the same Train data, and leaving the sweep out of N hides that selection.
- **m = every candidate in BH.** Rejected in §4 as double counting; kept as the fallback below.

## Consequences

### Positive

- One signature tests 100+ candidates across up to 12 families on server-01.
- A champion from a sweep has passed a DSR deflated for every variant tried, Validation, and BH.

### Negative

- Every sweep raises N for every later batch on this data. After one large sweep, small batches face a high bar.
- Many candidates and families mean fewer of them reach Validation, so a sweep often ends with no champion.
- More pipeline surface: grid expansion, chunked Train jobs, per-family Validation selection.

### Neutral

- ADR-026 batches of 2 to 6 candidates keep working unchanged.
- The OOS window stays sealed until certification.

## What would change this decision

- Evidence that the batch-wide N under-charges correlated grid points: then BH runs with m = every candidate, by
  decision record.
- A sweep whose champion fails its shadow badly: then the grid caps tighten.

## Build (after acceptance)

- Sweep front matter, grid expansion and `hermes batch check` support.
- Chunked Train jobs and per-family Validation selection in `backtest-batch.yml`.
- Champion step with m from §4; results job and Lab screen read per-candidate verdicts.
- The first sweep's families and cards, drafted from the literature and the swing-b1 and swing-b2 records.

## Cross-references

- [ADR-012](adr-012-continuous-ai-evaluation.md) — D-06 outcome blindness, D-11 trial counting
- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — the funnel floors and the Validation budget
- [ADR-023](adr-023-backtest-runs-on-server-01.md) — the runner and data on server-01
- [ADR-026](adr-026-research-batch-and-signed-champion-rule.md) — research batches and the champion rule
- [ADR-029](adr-029-one-hour-bars-for-intraday-holding-candidates.md) — 1h bars in all three environments
- Issues #1237, #1238 — 1h market data, replay and parity
- Issue #1280 — this ADR

## Sources

- Bailey, López de Prado — [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- Bailey, Borwein, López de Prado, Zhu — [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
- Benjamini, Hochberg — Controlling the False Discovery Rate (1995)
