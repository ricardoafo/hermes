---
document: Post-Mortem — Swing MVP1
version: TEMPLATE v1
status: DRAFT — fill in all fields before sign-off
based_on: pre-registration-v1.md §10
created: 2026-08-25
---

# Trial Termination Report — Swing MVP1

> Analogy: FDA clinical-trial termination report (IND safety report + final CSR wrap-up).
> This document is required within **7 days** of any kill trigger firing (§10).
> It is a binding experimental record. Do not adjust facts retroactively.
> Incomplete fields must be marked `UNKNOWN — see lab notebook entry [DATE]`.

---

## 1. Header

| Field | Value |
| ------- | ------- |
| **Kill trigger fired** | *(cite §10 exactly, e.g. "Live phase — Max drawdown 15% peak-to-trough")* |
| **Live metric value at kill** | *(e.g. "Drawdown: −16.3%")* |
| **Threshold from §10** | *(e.g. "15% peak-to-trough")* |
| **Timestamp of trigger (UTC)** | *(ISO 8601, e.g. "2026-10-14T09:32:00Z")* |
| **Phase at time of kill** | *(Backtest / Shadow / Live)* |
| **Lab notebook reference** | *(entry timestamp where kill was logged)* |
| **Days into phase** | *(e.g. "Day 43 of Shadow phase")* |
| **Capital deployed at kill** | *(e.g. "$4,850 USDT net of PnL")* |
| **Open positions at kill** | *(list pair + side + unrealized PnL each)* |
| **Regime at time of kill** | *(BTC regime label per regime-detection-notes.md — e.g. "bear-vol", "bull-quiet")* |

---

## 2. Kill Trigger Reference

This section lists all §10 triggers. Mark the one(s) that fired. Cross out the ones that did not fire.

### Backtest phase triggers (§10 ¶1)

- [ ] DSR on Train+Val < 0.7 — *actual value: ___*
- [ ] Any secondary metric fails — *which metric, actual value: ___*
- [ ] Max drawdown ≥ 15% during Train+Val — *actual value: ___*

### Shadow phase triggers (§10 ¶2)

- [ ] Sim-vs-live PnL divergence > 30 bps p50, sustained 7 rolling days — *actual p50: ___*
- [ ] Any SA-9 shock overlay rule fires 3+ times in 30 days — *count: ___, rules: ___*
- [ ] Shadow Sharpe < 0.3 for 60 consecutive days — *Sharpe on trigger day: ___*

### Live phase triggers (§10 ¶3)

- [ ] Max drawdown 15% peak-to-trough on experiment capital — *actual: ___*
- [ ] Time-to-recovery > 90 days from any drawdown ≥ 8% — *days elapsed: ___*
- [ ] 30 rolling trades OOS Sharpe < 0.3 — *actual Sharpe: ___*, *trade count: ___*
- [ ] SA-9 P0 rule violation — *which rule: ___*
  - [ ] Correlation lock breach (>0.80 threshold — §5.5)
  - [ ] Spread-spike
  - [ ] Depeg (USDT or asset)
  - [ ] Stale book
  - [ ] Exchange downtime > 5 min during open position
- [ ] Two consecutive weeks: live fills diverging > 50 bps from shadow — *actual drift: ___*

---

## 3. Timeline

Record what was observable at each waypoint. Pull from lab notebook entries. If the kill was in Backtest phase, the live-capital rows (T-1d onward) are N/A.

| Waypoint | UTC Date | Phase | Observable metric | Interpretation at the time |
| ---------- | ---------- | ------- | ------------------- | --------------------------- |
| **T-30d** | | | | |
| **T-14d** | | | | |
| **T-7d** | | | | |
| **T-1d** | | | | |
| **T-0 (fire time)** | | | | **Kill trigger fired.** |
| **T+1d** | | | *(positions closed, capital secured)* | |
| **T+7d** | | | *(this document completed)* | |

Notes on timeline:

- Were there any warning signs before T-0 that were noted but not acted on? If so, describe here.
- Were any kill-adjacent metrics tracked in the lab notebook that narrowly did NOT fire? List them.

---

## 4. Root Cause Analysis — 5 Whys

Use the classic 5-whys iterative method. Stop when you reach a root cause you could not have anticipated, or a system-level assumption that was wrong.

**Symptom (the kill trigger):** *(restate the metric and value)*

| Why # | Question | Answer |
| ------- | ---------- | -------- |
| Why 1 | Why did [kill trigger metric] breach [threshold]? | |
| Why 2 | Why did [answer to Why 1] happen? | |
| Why 3 | Why did [answer to Why 2] happen? | |
| Why 4 | Why did [answer to Why 3] happen? | |
| Why 5 | Why did [answer to Why 4] happen? | |

**Root cause statement (one sentence):** *(summarize — e.g. "The strategy has no edge in bear-vol regimes because Donchian breakouts generate false breakouts above EMA(200) during distribution phases.")*

**Category of root cause:**

- [ ] Strategy design flaw (signal logic)
- [ ] Threshold miscalibration (threshold too loose or too tight in §9/§10)
- [ ] Execution / fill quality (slippage model underestimated)
- [ ] Regime mismatch (edge only exists in specific regimes — see §4 regime coverage)
- [ ] Data quality issue
- [ ] Black swan / exogenous shock outside regime coverage
- [ ] Operational error (lab notebook deviation, engine bug)
- [ ] Other: ___

---

## 5. What the Pre-Registration Got Right / Wrong

Be specific. Cite section references. This is the scientific record of the protocol's prediction accuracy.

### Got right

| Claim in pre-registration | §ref | Evidence (what happened) |
|---------------------------|------|--------------------------|
|                           |      |                          |
|                           |      |                          |

### Got wrong (prediction failures)

| Claim in pre-registration | §ref | What actually happened | Severity of the miss |
|---------------------------|------|------------------------|----------------------|
|                           |      |                        |                      |
|                           |      |                        |                      |

### Thresholds that were too aggressive

e.g. "Ulcer index threshold of 10 (§9) — the strategy routinely ran at 11-12 in bull-quiet regime, which would have been acceptable; the threshold was too tight for crypto swing."

### Thresholds that were too lenient

e.g. "15% drawdown kill trigger (§10) fired only after −16.3%; at −12% we had already lost confidence."

---

## 6. Discipline Retrospective

This section documents whether the experimental discipline was maintained.

### Deviations attempted or completed

| Date (UTC) | Proposed or actual deviation | Pre-registration section affected | Outcome (blocked / executed / N/A) | Who flagged it |
|------------|------------------------------|-----------------------------------|------------------------------------|----------------|
|            |                              |                                   |                                    |                |

**Summary:**

- [ ] Zero deviations attempted — experiment ran clean.
- [ ] One or more deviations were PROPOSED but BLOCKED by integrity checker (Claude).
- [ ] One or more deviations were EXECUTED — this pre-registration is **INVALIDATED** for any executed deviation. List them above.

### Data-split discipline

- [ ] OOS window (2025-09-01 → 2026-12-31) was not inspected before certification. *(Confirm or explain.)*
- [ ] Certification gate (2027-01-01) was respected. *(Confirm or explain.)*
- [ ] Lab notebook entries are contiguous with no gaps. *(Confirm or explain.)*

### Integrity checker interactions

Describe any moments where Claude flagged a proposed deviation. Were the flags correct? Were any flags missed?

---

## 7. What v2 Pre-Registration Should Change

If you are NOT proceeding to v2 (H0 not rejected, strategy declared dead), write: `N/A — H0 not rejected, strategy doesn't work. No v2 planned.`

If you ARE proceeding to v2, use this table. Every row must cite the evidence from §§4–6 above.

| Pre-registration element | Current value (v1) | Proposed v2 value | Evidence / rationale |
| -------------------------- | ------------------- | ------------------- | ---------------------- |
| *e.g. Ulcer index threshold (§9)* | *< 10* | *< 12* | *Strategy ran at 11 in bull-quiet without degrading Win rate — threshold was too tight (§5 above)* |
| *e.g. Max drawdown kill trigger (§10)* | *15%* | *12%* | *At −12% we had already lost confidence; trigger came too late* |
| | | | |

### New hypotheses generated by this experiment

What unexpected patterns were observed that could be formalized as a new testable hypothesis in v2? Be specific and falsifiable.

---

## 8. Recovery Decisions

### Capital

| Question | Decision | Rationale |
| ---------- | ---------- | ----------- |
| What happens to the remaining experiment capital? | | |
| Is any capital re-allocated to a different strategy immediately? | | |
| Minimum recovery time before deploying capital again? | | |

### Timeline

| Question | Decision |
| ---------- | ---------- |
| Is v2 pre-registration going to be written? | Yes / No / Undecided |
| Earliest date v2 pre-registration could be signed | |
| Earliest date v2 backtest could begin (new Train/Val windows) | |

### Revised hypothesis (if proceeding to v2)

Write a one-paragraph revised hypothesis. It MUST be falsifiable and different from §1 of v1 in at least one concrete, measurable way. If you cannot write a revised hypothesis yet, write "DEFERRED — insufficient insight from this post-mortem."

---

## 9. Sign-Off

> This document is a scientific record. The operator's signature certifies that the facts above are accurate to the best of their knowledge and that no retroactive adjustments were made to the timeline or metrics.

| Field | Value |
| ------- | ------- |
| **Operator signature** | _______________ |
| **Date signed (UTC)** | _______________ |
| **Lab notebook entry confirming kill** | _______________ |
| **Git commit SHA of this document** | _______________ |
| **SHA256 of this document at signing** | _______________ |

---

*End of post-mortem template. Fill all fields. Incomplete post-mortems are not valid experimental records.*
