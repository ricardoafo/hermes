---
name: Pre-Registration — Swing MVP1
version: 1
supersedes: pre-registration-v0.md
author: Claude (drafted from research vault) — resolutions per operator confirmation 2026-08-25
date_drafted: 2026-08-25
date_signed: 2026-08-25T12:48:18Z
experiment_id: swing-mvp1-v1
status: SIGNED
signed_by: Rafo Afonso Montero <r.afonsomontero@gmail.com>
content_sha256: 8d73e99ec658712e2304b872e3231421a5f5eeeafbe8255b199cbb8092db3433
content_sha256_scope: git show 9ddb879:docs/experiments/swing-mvp1/pre-registration-v1.md | sha256sum
signing_commit_precursor: 9ddb879
---

# Pre-Registration Document — Swing Strategy MVP1

> This document is the SCIENTIFIC PROTOCOL for the first live-capital experiment of the hermes trading system. It is written BEFORE any parameter tuning, backtest optimization, or data inspection. **Once signed and hashed, any deviation from this document constitutes a new experiment with a new pre-registration.** This is the anti-p-hacking mechanism.
>
> Analogy: IRB-registered protocol of a clinical trial. Engine = apparatus. Operator = theorist. Claude = experimentalist + pre-registration integrity checker. This document = falsifiable claim.

---

## 1. Hypothesis

### Primary (H1)

The Swing strategy defined in §5 has statistically significant positive risk-adjusted return on the asset universe (§3), over the OOS test window (§4), NET of realistic fees and slippage (§6), at the position sizing (§5.4).

### Null (H0)

Swing strategy risk-adjusted return is indistinguishable from zero (or from a buy-and-hold benchmark) after transaction costs, over the same OOS window.

### Decision rule

- **H1 accepted** iff Deflated Sharpe Ratio (DSR) on OOS > 0.7 AND all secondary metrics (§9) pass.
- **H0 not rejected** otherwise → strategy declared UNTRUSTED → no live capital.

### What this experiment CANNOT answer

- Whether the strategy will work in future regimes not represented in the OOS window (non-stationarity risk).
- Whether the strategy is optimal — only whether it is positive.
- Whether it is the BEST strategy — only whether it is a viable one.

---

## 2. Experimental capital

| Parameter | Value | Rationale |
| ----------- | ------- | ----------- |
| Total experiment capital | **$5,000 USDT** | Smallest amount where fees do not dominate signal. |
| Denomination | USDT | Base of asset universe; SA-9 depeg overlay applies. |
| Funding schedule | — | Full $5k at experiment start OR tranches — operator's discretion — but pre-registered risk % refers to full $5k denominator regardless of actual deployed balance |
| Scaling post-certification | — | Post-CERTIFICATION scaling is a portfolio decision, NOT part of this experiment. Any capital change during the experiment invalidates results. |

---

## 3. Asset universe (FIXED)

| Symbol | Exchange | Rationale |
| -------- | ---------- | ----------- |
| BTC/USDT | Binance spot | Highest liquidity; base benchmark. |
| ETH/USDT | Binance spot | Second-largest; independent staking/DeFi flow. |
| SOL/USDT | Binance spot | Higher-vol third leg; captures alt-season regime. |

- No additions permitted during the experiment.
- If a pair delists mid-experiment, position closes and pair excluded from remaining OOS window; result annotated.
- **Binance-only for MVP1** (OD-6 → Kraken deferred to MVP2).

---

## 4. Data windows (FROZEN)

All windows are UTC, inclusive of both endpoints.

| Window | Start | End | Duration | Purpose | Access rule |
| -------- | ------- | ----- | ---------- | --------- | ------------- |
| **Train** | 2020-09-01 | 2024-06-30 | ~46 months | Parameter tuning, signal exploration | Full read/write during backtest development |
| **Validation** | 2024-07-01 | 2025-08-31 | ~14 months | Model selection between competing versions | Touch sparingly; every touch logged in lab notebook |
| **OOS Test** | **2025-09-01** | **2026-12-31** | **~16 months** | Certification — H1 vs H0 | **Touched EXACTLY ONCE at certification.** Code physically refuses to read this window outside certification mode. |

**Uniform start date across all pairs.** BTC and ETH have data back to 2017, but SOL only lists on Binance in September 2020. To keep the three-asset universe on identical windows (no per-pair regime bias), Train begins 2020-09-01 for all pairs. Consequence: the 2020 Q1 COVID crash is excluded from Train — accepted trade-off for windowing uniformity.

### Discipline gate

**Certification cannot begin before 2027-01-01** — the date on which all OOS data will exist. If backtest development finishes early, **we wait.** Real clinical trials wait for the last patient. Same rule.

### Regime coverage

Train + Validation + OOS spans:

- 2020 Q4–2021: bull peak
- 2022: bear + Terra/Luna + FTX
- 2023: SVB/USDC depeg + recovery
- 2024: ETF launch, Aug 5 volatility
- 2025–2026: current regime + OOS window

If any regime is later found underrepresented, the pre-registration is invalidated — not extended.

### Dataset fingerprints

Pending Week 1-2 data ingest. Every Parquet/SQLite file receives a SHA256 fingerprint stored in `docs/experiments/swing-mvp1/data-manifest.json`. The manifest's hash is included in this document's signature.

---

## 5. Strategy specification (FROZEN)

Source: SA-7 (Donchian + Chandelier + ADX + EMA regime filter).

### 5.1 Entry (long-only for MVP1)

- **Donchian breakout**: close > highest_high(20) on 4h timeframe
- **Regime filter**: close > EMA(200) on 1D timeframe
- **Momentum gate**: ADX(14) ≥ 25 on 4h timeframe
- All three conditions TRUE simultaneously on bar close.

### 5.2 Exit

- **Chandelier Exit**: trailing stop at highest_high(22) − 3 × ATR(22) on 4h
- **Time stop**: close position if not stopped out within **90 days** (Swing horizon per Faber/Clenow trend-following literature — 60-120 days for average winners)
- **Regime break**: close if close < EMA(200) on 1D

### 5.3 Order type

- Entry: **market order** at bar close of signal bar
- Exit: **on-exchange stop-market** at chandelier level (posted immediately after entry fill; moved daily as trailing stop advances)

### 5.4 Position sizing

- **Risk per trade**: 1% of experiment capital ($50)
- **Position notional**: $50 / (entry_price − chandelier_stop_price) × entry_price
- **Hard cap**: 5% of experiment capital per position ($250 notional max)
- **Kelly deferred**: Fractional Kelly (SA-1) NOT applied for MVP1 — activates only after ≥ 30 OOS trades exist (SYNTHESIS T-1)

### 5.5 Concurrency + correlation

- **Max concurrent positions**: 3 (one per pair)
- **Correlation lock (SA-9 RO-01)**: if 30-day rolling correlation between two open pairs exceeds **0.80**, block new entry in the third correlated pair (existing positions unaffected). Firing at 0.80 vs 0.85 accepts that above 0.80, diversification is illusory — during panic is exactly when we want the lock to hold.
- **Total exposure cap**: 15% of capital notional (3 × 5%)

---

## 6. Fee + execution model

### 6.1 Fees (point-in-time)

- Binance retail tier (VIP0) at the timestamp of each historical trade
- **BNB discount OFF** (conservative)
- Fee history reconstructed from Wayback Machine into `docs/experiments/swing-mvp1/fee-history.csv`

### 6.2 Slippage model (backtest)

- OHLCV-only backtest (OD-1 resolution: Option C for backtest + Option B L2 collector concurrent for shadow)
- Entry slippage: 5 bps over bar close
- Exit slippage on stop: 15 bps over stop trigger price (stop-hunt + gap risk)
- Shadow phase replaces this with `OrderbookWalkFillModel` fed by live L2 collector

### 6.3 Fill assumptions

- No partial fills in backtest (order fills fully at slippage-adjusted price OR is rejected)
- No re-quote logic; if PRICE_FILTER would reject, trade is skipped (SA-3)

---

## 7. Certification lifecycle

| State | Entry criteria | Exit criteria |
| ------- | ---------------- | --------------- |
| `UNTRUSTED` | Default | See below |
| `BACKTEST_CERTIFIED` | Backtest metrics (§9) pass on Train+Val | Any parameter change → back to UNTRUSTED |
| `SHADOW_CERTIFIED` | 90-day live shadow: sim-vs-live drift < 30 bps p50 (SA-5) | Drift breach → back to UNTRUSTED |
| `OOS_CERTIFIED` | H1 accepted on OOS (§1) — one-shot test on or after 2027-01-01 | Cannot revert; result is permanent for this experiment |
| `LIVE_APPROVED` | OOS_CERTIFIED + operator sign-off | Any kill trigger (§10) → HALT |
| `CERTIFIED` | 90-day live capital with no kill trigger fired | Ongoing monitoring per §10 |

---

## 8. Statistical framework

### 8.1 Primary test

- **Deflated Sharpe Ratio (DSR)** — López de Prado (2014). Adjusts for sample length, skewness, kurtosis, and NUMBER OF TRIALS. Every backtest run during train + val counts as a trial; total logged in lab notebook.

### 8.2 Confidence intervals

- **Bootstrap CI** on OOS Sharpe: 10,000 resamples, block bootstrap with block length 30 days (preserves autocorrelation)
- H1 accepted only if lower bound of 95% CI > 0

### 8.3 Cross-validation (train + val ONLY — NEVER OOS)

- **Walk-forward**: expanding window, retrain every 6 months, test on next 3 months
- **Purged combinatorial cross-validation** (López de Prado): 5-fold, 5-day purge + 3-day embargo around fold boundaries

### 8.4 Multiple-testing correction

- If any secondary metric is added after v1 signing, Bonferroni correction is applied
- Every added test logged with penalty in lab notebook

---

## 9. Metrics + thresholds

### Primary (must pass for LIVE_APPROVED)

| Metric | Threshold | Rationale |
| -------- | ----------- | ----------- |
| DSR on OOS | > 0.7 | SA-5 default; retail-viable floor (institutional target > 0.95) |
| Bootstrap 95% CI lower bound on OOS Sharpe | > 0 | Ensures Sharpe > 0 is not statistical noise |
| OOS trade count | ≥ 30 | Minimum sample for CI meaningfulness |

### Secondary (all must pass)

| Metric | Threshold | Rationale |
| -------- | ----------- | ----------- |
| Max drawdown (OOS) | < 15% | Kill trigger; if backtest breaches, live breaches worse |
| Time-to-recovery from any drawdown ≥ 8% | < 90 days | Psychological survivability |
| Profit factor | > 1.3 | Winners/losers ratio with margin above break-even |
| Ulcer index | **< 10** | Crypto Swing is inherently ulcer-heavy (reference: S&P 60/40 runs 1-3, aggressive trend-following 8-12, crypto buy-hold 15-25). 10 = honest crypto-Swing threshold. |
| Total fees / gross PnL | < 30% | Above 30% → fragile to fee-tier changes |
| Win rate | ≥ 30% | Below 30% requires unrealistically asymmetric R:R (typically > 4:1) |

---

## 10. Kill criteria

### Backtest phase (do not proceed to shadow)

- DSR on Train+Val < 0.7
- Any secondary metric fails
- Max drawdown ≥ 15% during train+val

### Shadow phase (do not proceed to live)

- Sim-vs-live PnL divergence > 30 bps p50 sustained 7 rolling days (SA-5)
- Any SA-9 shock overlay rule fires 3+ times in 30 days
- Shadow Sharpe < 0.3 for 60 consecutive days

### Live phase (HALT — declare strategy dead)

- **Max drawdown 15% peak-to-trough** on experiment capital
- Time-to-recovery > 90 days from any drawdown ≥ 8%
- 30 rolling trades OOS Sharpe < 0.3
- Any SA-9 P0 rule violation (correlation lock breach, spread-spike, depeg, stale-book, exchange downtime > 5 min during open position)
- Two consecutive weeks of live fills diverging > 50 bps from same-strategy shadow

### Post-kill protocol

- Kill = strategy DEAD for this pre-registration
- Post-mortem within 7 days
- New pre-registration required to attempt a variant

---

## 11. Deviation protocol

### Counts as deviation (INVALIDATES this pre-registration)

- Any change to §3 asset universe
- Any change to §4 data windows
- Any change to §5 strategy spec
- Any change to §6 fee or slippage model
- Any change to §9 primary metric or threshold
- Any change to §10 kill criteria

### Does NOT count as deviation

- Adding a secondary metric (permitted with Bonferroni penalty logged)
- Engine bug fixes that do not change strategy math
- Data ingest updates (new bars accrue in real time; historical never modified)

---

## 12. Experiment lab notebook

Location: `docs/experiments/swing-mvp1/lab-notebook.md`
Format: append-only, event-sourced (extends SA-8 event log discipline)
Every entry: UTC ISO 8601 timestamp, phase (train/val/OOS/shadow/live), action, observed metrics, decision, rationale.

**This is the anti-p-hacking mechanism.** If a metric result triggers a rules deviation, the notebook records it and the pre-registration is invalidated.

---

## 13. Roles

| Role | Person | Responsibility |
| ------ | -------- | ---------------- |
| **Theorist (sole)** | Operator | Owns strategy hypothesis, sets thresholds, signs pre-registration, makes kill-trigger calls, decides deviations (knowing each invalidates the experiment) |
| **Experimentalist** | Engine (code) | Runs backtest/shadow/live, enforces data-split walls, reports metrics without interpretation |
| **Pre-registration integrity checker** | Claude | Flags every proposed deviation LOUDLY. When operator says "let me tweak the ADX filter," response is "that invalidates the experiment — write it down or don't do it." Mimics IRB independent-review function. |

- Theorist does not touch OOS data before certification.
- Experimentalist does not adjust thresholds or kill criteria mid-experiment.
- Integrity checker has no execution authority — only escalation.

---

## 14. Companion documents (referenced but separate)

| Doc | Location | Status |
| ----- | ---------- | -------- |
| Data manifest (SHA256 fingerprints) | `docs/experiments/swing-mvp1/data-manifest.json` | Pending Week 1-2 ingest |
| Fee history | `docs/experiments/swing-mvp1/fee-history.csv` | Pending Week 2 reconstruction |
| Lab notebook | `docs/experiments/swing-mvp1/lab-notebook.md` | Created at v1 signing (empty, append-only) |
| **Success criteria + progress measurement** | `docs/experiments/swing-mvp1/success-criteria.md` | **TBD — companion doc, not part of scientific protocol.** Covers engineering milestones, weekly progress reporting, operational KPIs. Owned separately because it evolves with engineering execution and does not constrain the experiment. |
| Post-mortem template | `docs/experiments/swing-mvp1/post-mortem-template.md` | To be created before certification |

---

## 15. Change log

| Version | Date       | Change                                                                                                                                                                                                                                       |
|---------|------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| v0      | 2026-08-25 | Initial draft — 6 open items flagged in §15 for operator resolution                                                                                                                                                                          |
| v1      | 2026-08-25 | Resolutions baked in: OOS 2025-09-01 → 2026-12-31 with 2027-01-01 certification gate; universe unchanged; correlation lock 0.80 (was 0.85); ulcer 10 (was 8); time-stop 90d (was 30d); role model formalized with integrity-checker function |

---

## 16. Signatures

**Signed 2026-08-25T12:48:18Z.**

- Operator: Rafo Afonso Montero (`r.afonsomontero@gmail.com`)
- Timestamp: `2026-08-25T12:48:18Z`
- Content SHA256 (pre-signing state at commit `9ddb879`): `8d73e99ec658712e2304b872e3231421a5f5eeeafbe8255b199cbb8092db3433`
- SHA256 of `data-manifest.json`: TBD — Week 1-2 ingest
- Git commit SHA of pre-signing state: `9ddb879`
- Git commit SHA of signing state: recorded in the commit message of this change

**Verification protocol.** The content hash above is of the file's state in commit `9ddb879` (immediately before this signing edit). To verify:

```bash
git show 9ddb879:docs/experiments/swing-mvp1/pre-registration-v1.md | sha256sum
# expect: 8d73e99ec658712e2304b872e3231421a5f5eeeafbe8255b199cbb8092db3433
```

Any subsequent edit to this file changes the git tree — the signing commit is the anchor, and any post-signing modification is a **new experiment (v2)** per §11 Deviation Protocol.

**This document is now IMMUTABLE.** Any change means v2 — which kills v1's experiment and starts a new one.

---

**End of pre-registration v1 (pending signature).**

Next steps:

1. Operator reviews v1 for any final concerns.
2. Operator signs § 16 (with a date and cryptographic hash — I can compute SHA256 on request).
3. Git commit is made.
4. Companion `success-criteria.md` is drafted separately (parked).
5. Data ingest begins (Week 1-2), populates `data-manifest.json`.
6. THEN spec work begins, knowing exactly what to support.
