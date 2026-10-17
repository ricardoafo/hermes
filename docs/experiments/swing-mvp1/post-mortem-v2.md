---
document: Post-Mortem — Swing MVP1 v2
version: 1
status: DRAFT — Code Owner sign-off pending (§9)
based_on: pre-registration-v2.md §10, post-mortem-template.md
experiment_id: swing-mvp1-v2
created: 2026-10-17
due: 2026-10-24
---

# Trial Termination Report — Swing MVP1 v2

> Written within 7 days of the kill, per pre-registration v2 §10 (post-kill protocol).
> The author is an AI session, which sees each gate check as PASS or FAIL only (ADR-012 D-06, ADR-023 §7).
> Every metric value below is therefore marked `WITHHELD`; the values are in the `certify.txt` file of backtest
> workflow run 39056430551's artifact, which only the Code Owner opens.
> Statements marked *(inferred)* are reasoning from the PASS/FAIL pattern and the v1 to v2 changes, not measurements.

---

## 1. Header

| Field | Value |
| ------- | ------- |
| **Kill trigger fired** | §10 Backtest phase: DSR < 0.95, and five funnel checks failed (see §2) |
| **Metric values at kill** | WITHHELD from the AI author; `certify.txt` in run 39056430551 |
| **Thresholds from §10** | DSR ≥ 0.95; funnel thresholds in the pre-registration front matter `backtest_funnel` |
| **Timestamp of trigger (UTC)** | 2026-10-17T18:07:43Z (Train verdict logged); kill confirmed 2026-10-17T20:56:00Z (Code Owner declined Validation) |
| **Phase at time of kill** | Backtest (Train only; no Validation run) |
| **Lab notebook reference** | Entries 2026-10-17T18:07:43Z (`train`, GATE FAILED) and 2026-10-17T20:56:00Z (`train`, kill decision) |
| **Days into phase** | Day 0: the first logged v2 run |
| **Capital deployed at kill** | None. v2 never reached shadow or live |
| **Open positions at kill** | None |
| **Regime at time of kill** | N/A (backtest kill; no live market exposure) |

---

## 2. Kill Trigger Reference

v2's backtest gate is §10 of pre-registration v2 (stricter than the v1 gate in the template). Train window
2020-09-01 to 2024-06-30, run on main at 25c795d on server-01, clean tree, 8 trials logged.

### Backtest phase triggers (§10)

- [x] DSR < 0.95 — FAIL, value WITHHELD
- [ ] Any §9 secondary metric fails — all PASS (max_drawdown, recovery_days, profit_factor, ulcer_index,
  fees_to_gross_pnl, win_rate, calibration_fills)
- [ ] Max drawdown ≥ 15% — PASS
- [x] Funnel: PBO (CSCV, 16 blocks) ≤ 0.05 — FAIL
- [x] Funnel: t-statistic of daily returns > 3 — FAIL
- [ ] Funnel: trades ≥ 100 — PASS
- [ ] Funnel: cost stress (fees and slippage × 2) total return still positive — PASS
- [x] Funnel: more than half of calendar years positive — FAIL
- [ ] Funnel: bull stretches compounded return positive — PASS
- [x] Funnel: bear stretch 2021-11-11..2022-11-21 compounded return positive — FAIL
- [x] Funnel: Validation annualised return at least half of Train's — FAIL, but by construction: a Train-only run has
  no Validation stretch (issue #1037). It does not decide the verdict.
- [ ] Funnel: each §8.5 neighbour's Sharpe at least half the base — PASS
- [ ] Funnel: correlation with running strategies below 0.7 — PASS

### Shadow phase and live phase triggers

N/A: v2 never started shadow.

---

## 3. Timeline

| Waypoint | UTC Date | Phase | Observable | Interpretation at the time |
| ---------- | ---------- | ------- | ------------------- | --------------------------- |
| **v1 gate** | 2026-10-08 | Backtest (v1) | v1 GATE FAILED on Train + Validation (issue #735) | v1 not certified; the #735 audit found engine defects |
| **#785 fix** | 2026-10-17 11:50 | Shadow (v1) | §11 deviation logged for the stop fix | Defects to be fixed in a new pre-registration |
| **v2 signed** | 2026-10-17 13:31 | Pre-registration | content_sha256 212a109f… | v2 = v1 with defects fixed, close-based exits, stricter gate |
| **v2 Train run** | 2026-10-17 ~18:07 | Backtest (v2) | GATE FAILED: DSR and 5 funnel checks FAIL, rest PASS | Train fails on the checks that measure edge strength and regime robustness |
| **T-0 (kill)** | 2026-10-17 20:56 | Backtest (v2) | Code Owner declines a Validation touch | **Kill confirmed.** 3 Validation touches unspent |
| **T+7d** | by 2026-10-24 | Post-mortem | This document signed | |

Notes on timeline: v2 was drafted, signed, built and run on one day. Every v2 engineering PR merged before the run;
no code changed between the run and the kill.

---

## 4. Root Cause Analysis — 5 Whys

**Symptom (the kill trigger):** DSR below 0.95 on Train, with PBO, t-statistic, positive years and bear return also
failing. Values WITHHELD.

| Why # | Question | Answer |
| ------- | ---------- | -------- |
| Why 1 | Why did DSR fail? | The Sharpe ratio, deflated for the number of trials and the return distribution, is not high enough. The t-statistic failing too says the daily return mean is not clearly above zero *(inferred from the two FAILs together)*. |
| Why 2 | Why is the mean daily return weak? | The returns are uneven across time: the bull stretches pass but the bear stretch and the share of positive calendar years fail *(observed as PASS/FAIL)*. |
| Why 3 | Why does the bear stretch lose? | The long-only breakout keeps entering in a bear market whenever a bounce closes above the daily EMA(200), and those breakouts fail *(inferred: the regime filter is the only bear defence, and it passes during bear rallies)*. |
| Why 4 | Why is that not offset elsewhere? | With only three highly correlated large caps and a 3-position cap, gains are concentrated in the two bull stretches; PBO failing says the ranking of the trial set is not stable across time blocks *(inferred)*. |
| Why 5 | Why did the pre-registration expect otherwise? | v1's hypothesis assumed a trend-following edge across regimes on these pairs. v2 fixed engine defects and made exits executable but kept that hypothesis. The fixes repaired the secondary metrics (all PASS in v2) without creating an edge strong enough for the stricter gate *(inferred)*. |

**Root cause statement (one sentence):** *(inferred)* A long-only Donchian breakout on BTC, ETH and SOL earns in bull
stretches but not in the 2021-22 bear stretch, so its risk-adjusted return over Train is too weak and too
regime-dependent to clear DSR ≥ 0.95.

**Category of root cause:**

- [x] Strategy design flaw (signal logic)
- [ ] Threshold miscalibration (threshold too loose or too tight in §9/§10)
- [ ] Execution / fill quality (slippage model underestimated)
- [x] Regime mismatch (edge only exists in specific regimes)
- [ ] Data quality issue
- [ ] Black swan / exogenous shock outside regime coverage
- [ ] Operational error (lab notebook deviation, engine bug)
- [ ] Other: ___

Execution is not ticked: cost stress at 2× fees and slippage passed.

---

## 5. What the Pre-Registration Got Right / Wrong

### Got right

| Claim in pre-registration | §ref | Evidence (what happened) |
| --- | --- | --- |
| v2 fixes known engine defects without changing the hypothesis | §0 | All seven §9 secondary metrics PASS; v1 had failed recovery_days, profit_factor and fees_to_gross_pnl |
| The edge, if any, survives realistic costs | §6, §8.5 | Cost stress (× 2) PASS |
| Results are not a knife-edge parameter choice | §8.5 | Every neighbour's Sharpe PASS against the base |
| Enough trades to judge | §10 funnel | Trade count PASS |
| "v2 passing would be consistent with [v1's defects causing the failure], not proof" | §1 | v2 failed, so the defects were not the whole story |

### Got wrong (prediction failures)

| Claim in pre-registration | §ref | What actually happened | Severity of the miss |
| --- | --- | --- | --- |
| H1: a statistically significant positive risk-adjusted return | §1 | DSR and t-statistic FAIL on Train | Fatal for v2 |
| The regime filter (daily close > EMA(200)) keeps the strategy out of bear markets | §5.1 | Bear stretch FAIL | Fatal: the main design assumption |
| The gate can be read on a Train-only run | §10 | `validation_vs_train` cannot pass without Validation (issue #1037) | Low: reporting defect, not a verdict change |

### Thresholds that were too aggressive

None found. No threshold is proposed for loosening: DSR ≥ 0.95 and the funnel are ADR-021 §5 rules for every strategy
from v2 on, and loosening them after a fail would be fitting the gate to the strategy.

### Thresholds that were too lenient

None found.

---

## 6. Discipline Retrospective

### Deviations attempted or completed

| Date (UTC) | Proposed or actual deviation | Pre-registration section affected | Outcome (blocked / executed / N/A) | Who flagged it |
| --- | --- | --- | --- | --- |
| — | None | — | N/A | — |

**Summary:**

- [x] Zero deviations attempted — experiment ran clean.
- [ ] One or more deviations were PROPOSED but BLOCKED by integrity checker (Claude).
- [ ] One or more deviations were EXECUTED.

### Data-split discipline

- [x] OOS window (2025-09-01 → 2026-12-31) was not inspected. The run read Train only.
- [x] Certification gate (2027-01-01) was respected.
- [x] Lab notebook entries are contiguous; `LabNotebook.verify_file()` reports every entry valid.
- [x] No Validation touch was spent; all 3 remain for a future pre-registration's use under its own rules.

### Integrity checker interactions

- One process slip, logged and disclosed to the Code Owner on 2026-10-17: the AI session downloaded run 39056430551's
  artifact and job log to read PASS/FAIL and copy the trial lines. No value was printed, put in a prompt or memory,
  and the copies were deleted, but the download itself breaks ADR-023 §7. From now on trial lines are fetched by the
  Code Owner or by a workflow step.
- The workflow's Certify step ended red on a valid GATE FAILED verdict because the runner shell is `bash -e`
  (issue #1036, PR #1039). The Funnel step was skipped as a result; certify already evaluates every funnel row, so
  the verdict is complete.

---

## 7. What a v3 Pre-Registration Should Change

The Code Owner decides whether a v3 is written. If none is planned: `N/A — H0 not rejected for this design.`

If a v3 is written, the evidence above points at the hypothesis, not the thresholds. Each row is a candidate for the
Code Owner, not a decision, and every trial a v3 logs joins the same trial log and DSR count.

| Pre-registration element | Current value (v2) | Candidate v3 direction | Evidence / rationale |
| -------------------------- | ------------------- | ------------------- | ---------------------- |
| Bear defence (§5.1) | Daily close > EMA(200) only | A regime rule that also stays flat in bear rallies, or a different idea family from issue #361 | Bear stretch and positive-years FAIL (§4 Why 3) |
| Universe (§3) | BTC, ETH, SOL | Unchanged unless a new family needs it | Narrow, correlated universe concentrates gains (§4 Why 4, inferred) |
| Gate thresholds (§10) | DSR ≥ 0.95 + funnel | Unchanged | §5: none found too aggressive |
| Train-only reporting | `validation_vs_train` FAIL | Report it as not applicable on Train-only runs | Issue #1037 |

### New hypotheses generated by this experiment

- *(inferred)* On BTC, ETH and SOL at 4h, a breakout entry gated by a daily trend filter makes money only while the
  daily trend is up and persistent; a filter that also requires a rising EMA(200) slope would stay flat through the
  2021-22 bear stretch and turn the bear check positive or flat, with fewer trades. Falsifiable on Train by the
  bear-stretch and positive-years checks; it would need its own signed pre-registration.

---

## 8. Recovery Decisions

### Capital

| Question | Decision | Rationale |
| ---------- | ---------- | ----------- |
| What happens to the remaining experiment capital? | Untouched: no capital was ever deployed for v2 | Backtest-only kill |
| Is any capital re-allocated to a different strategy immediately? | No | No strategy has passed a gate |
| Minimum recovery time before deploying capital again? | Until a strategy passes its gate, shadow (ADR-021 §5) and OOS certification | ADR-021 lifecycle |

The v1 shadow is retired separately on the Code Owner's decision of 2026-10-17; with v2 dead, nothing replaces it for
now.

### Timeline

| Question | Decision |
| ---------- | ---------- |
| Is v3 pre-registration going to be written? | Undecided — Code Owner's call |
| Earliest date v3 pre-registration could be signed | Any time after this post-mortem is signed |
| Earliest date v3 backtest could begin | After its pre-registration is signed; Train and Validation windows unchanged, OOS sealed until 2027 |

### Revised hypothesis (if proceeding to v3)

DEFERRED — the direction is in §7, but the Code Owner chooses the idea family first (issue #361).

---

## 9. Sign-Off

> This document is a scientific record. The operator's signature certifies that the facts above are accurate to the
> best of their knowledge and that no retroactive adjustments were made to the timeline or metrics.

| Field | Value |
| ------- | ------- |
| **Operator signature** | _______________ |
| **Date signed (UTC)** | _______________ |
| **Lab notebook entry confirming kill** | 2026-10-17T20:56:00Z |
| **Git commit SHA of this document** | _______________ |
| **SHA256 of this document at signing** | _______________ |
