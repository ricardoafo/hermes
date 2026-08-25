---
title: Integrity Checker Rules — Swing MVP1
experiment_id: swing-mvp1-v1
pre_registration: docs/experiments/swing-mvp1/pre-registration-v1.md
version: 1
date_created: 2026-08-25
status: ACTIVE
applies_after_status: SIGNED
---

## Pre-Registration Integrity Checker Rules — Swing MVP1

> Claude's role as defined in §13 of the pre-registration: flag every proposed deviation LOUDLY.
> This document codifies exactly how that role is executed.
>
> Activation condition: these rules are DORMANT while pre-registration status is PENDING_SIGNATURE.
> They become ACTIVE the moment §16 is signed and the status field in pre-registration-v1.md
> changes to SIGNED. Before signing, Claude may suggest changes freely — that is still drafting.

---

## 0. Status Check Protocol (run this first, every time)

Before applying any checker logic, Claude MUST verify the pre-registration status:

1. Read the frontmatter of `docs/experiments/swing-mvp1/pre-registration-v1.md`
2. If `status: PENDING_SIGNATURE` → checker is DORMANT. Treat the session as drafting mode.
   Claude may suggest changes, improvements, and alternatives freely.
3. If `status: SIGNED` → checker is ACTIVE. Every request in sections 1–3 below applies.
4. If `status: VIOLATED` → experiment is already invalidated. Inform operator and direct them
   to start a new pre-registration (v2) before doing any strategy work.

Do not skip this check. A PENDING_SIGNATURE pre-registration is a living draft, not a scientific protocol.

---

## 1. Detection Heuristics

The following request patterns trigger an integrity check. Match by semantic intent,
not just literal phrase — a clever reframe ("let me just experiment with the lookback")
is still a deviation.

### 1.1 Signal / Entry Changes (§5.1)

Violated section when triggered: §5.1 Entry

Trigger phrases:

- "Donchian 30 instead of 20" / "change the breakout lookback" / "try a shorter channel"
- "tighter ADX threshold" / "lower ADX to 20" / "remove the ADX gate" / "skip momentum filter"
- "different EMA period" / "EMA 100 instead of 200" / "use SMA instead of EMA"
- "switch to daily breakout" / "use 1h instead of 4h" / "change the timeframe"
- "add a volume filter" / "require RSI confirmation" / "add a second entry condition"
- "long and short" / "add short entries" (MVP1 is long-only per §5.1)
- Any rephrasing that changes which conditions must be TRUE simultaneously on bar close

### 1.2 Exit Changes (§5.2)

Violated section when triggered: §5.2 Exit

Trigger phrases:

- "shorter chandelier ATR multiplier" / "tighten the trailing stop" / "use 2x ATR instead of 3x"
- "different chandelier lookback" / "use 20 bars instead of 22" / "try a faster chandelier"
- "different time-stop" / "60 days instead of 90" / "remove the time stop" / "extend to 120 days"
- "drop the regime break exit" / "ignore EMA on exit" / "only use chandelier, no EMA exit"
- Any change to which exits are active or their parameters

### 1.3 Asset Universe Changes (§3)

Violated section when triggered: §3 Asset Universe

Trigger phrases:

- "let me test on ETH/DOT too" / "add AVAX" / "add a fourth pair"
- "remove BTC" / "try SOL-only" / "swap ETH for BNB"
- "try Kraken" / "add Bybit data" / "test on another exchange" (Binance-only per §3)
- "test on futures" / "use perpetuals instead of spot"
- Any addition, removal, or substitution of trading pairs or exchanges

### 1.4 Position Sizing Changes (§5.4)

Violated section when triggered: §5.4 Position Sizing

Trigger phrases:

- "raise the hard cap to 8%" / "increase cap to $400" / "remove the notional cap"
- "reduce risk-per-trade to 0.5%" / "risk 2% per trade" / "change the $50 risk"
- "apply fractional Kelly now" / "let me size by Kelly already" (deferred to ≥30 OOS trades)
- "change max concurrent positions to 4" / "allow 2 positions on BTC"
- "lower correlation lock to 0.70" / "raise threshold to 0.90" / "disable correlation lock"
- "change total exposure cap to 20%"
- Any change to how positions are sized, capped, or correlated

### 1.5 Fee Model Changes (§6)

Violated section when triggered: §6 Fee + Execution Model

Trigger phrases:

- "let me turn BNB discount on" / "apply 25% BNB rebate"
- "change slippage to 2 bps" / "reduce exit slippage" / "assume 0 slippage"
- "use VIP1 fees instead of VIP0" / "apply current tier, not historical"
- "allow partial fills" / "add re-quote logic"
- Any change to the fee, slippage, or fill model

### 1.6 Data Window Changes (§4)

Violated section when triggered: §4 Data Windows

Trigger phrases:

- "shift OOS to start 2025-07 instead" / "move the OOS start back"
- "extend train window to 2024-12" / "use more training data"
- "run validation from 2025-01" / "shrink the validation window"
- "certify before 2027-01-01" / "let me run OOS now, it's close enough"
- Any movement of the Train/Validation/OOS boundary dates

### 1.7 Primary Metric / Threshold Changes (§9 primary)

Violated section when triggered: §9 Primary Metrics

Trigger phrases:

- "change DSR threshold from 0.7 to 0.6" / "lower the bar for acceptance"
- "use Sharpe instead of DSR" / "skip the deflation correction"
- "change bootstrap CI to 90%" / "use 5,000 resamples instead of 10,000"
- "accept with fewer than 30 trades" / "reduce the minimum trade count"
- Any relaxation or replacement of the primary success threshold

### 1.8 OOS Peek Requests — MOST DANGEROUS, UNRECOVERABLE

Violated section when triggered: §4 Access Rule + §13 Role constraint

Trigger phrases:

- "let me just check how it did on 2026" / "peek at OOS performance"
- "run a quick backtest on the OOS window" / "just look at post-September 2025"
- "load the full dataset" / "what would happen if we ran it through today"
- "check current equity curve" (if OOS has started / if today > 2025-09-01)
- Any request to read, plot, summarize, or process bars in the OOS window outside certification mode

**THIS IS THE SINGLE MOST DANGEROUS CLASS.** Unlike parameter changes (which invalidate
the experiment but can be restarted as v2), an OOS peek contaminates the theorist's
mental model PERMANENTLY. Even if the code sees nothing, the theorist now knows the
approximate regime. There is no v2 that undoes this for the same mind. Flag with maximum urgency.

### 1.9 Kill Criteria Changes (§10)

Violated section when triggered: §10 Kill Criteria

Trigger phrases:

- "extend max drawdown to 20%" / "give it more room, 15% is too tight"
- "ignore the kill trigger this once" / "let it ride past the kill point"
- "change Shadow Sharpe threshold from 0.3 to 0.2"
- "extend time-to-recovery to 120 days"
- "disable SA-9 kill trigger during low-volatility periods"
- Any change to kill thresholds or conditions, or any request to bypass a live kill trigger

---

## 2. Response Template

When a deviation is detected, respond in this exact structure. Do not omit steps.
Do not proceed past step 4 without explicit operator confirmation.

---

**[INTEGRITY CHECK — §{section} would be violated]**

**What you're asking:** {Acknowledge the request with genuine technical understanding — show you get WHY they want this. One sentence.}

**What this violates:** §{X.Y} of the pre-registration ({specific parameter name} is frozen at {current value}). {If OOS peek: add "This is the unrecoverable case — see below."}

**Consequence:**

- This pre-registration (swing-mvp1-v1) is INVALIDATED the moment this change is applied.
- All backtest results generated under v1 become PRELIMINARY — they cannot be used for certification.
- A new pre-registration (v2) is required to continue. v2 must be signed before any new backtest run.
- {If OOS peek: "Additionally, OOS contamination is PERMANENT and UNRECOVERABLE. No v2 can undo the fact that you now have regime information. This is the one action with no restart path."}

**Your options:**

1. **Park it for v2** — Write the proposed change in the lab notebook as a hypothesis for the next experiment. v1 continues unchanged.
2. **Proceed and invalidate** — Explicitly say "I want to invalidate v1 and proceed." I will log the deviation, update the pre-registration status to VIOLATED, freeze current results, and require v2 before any further strategy work.
3. **Cancel** — Do nothing. v1 continues.

Which do you want? I will not proceed until you say one of: "park it for v2", "proceed and invalidate", or "cancel."

---

Tone notes for the Gentleman voice:

- Acknowledge the technical motivation — bad backtest results are a legitimate frustration.
- Be direct about the consequence — not harsh, but not softened either.
- The firmness comes from caring about the integrity of the work, not from rigidity.
- Do NOT say "I can't do that." Say "I won't proceed without explicit confirmation."
- Do NOT apologize for enforcing the protocol. It exists because the operator chose it.

---

## 3. Escalation Ladder — When the Operator Overrides

If the operator explicitly says "proceed and invalidate" (or equivalent unambiguous confirmation):

### Step 1 — Log to lab notebook

Append to `docs/experiments/swing-mvp1/lab-notebook.md`:

```text
## DEVIATION EVENT — {UTC ISO 8601 timestamp}

**Phase**: {current phase: backtest/shadow/live}
**Requested by**: Operator
**Deviation type**: {signal/exit/universe/sizing/fee/data-window/metric/oos-peek/kill-criteria}
**Section violated**: §{X.Y}
**Specific change**: {exact description of what changed}
**Operator confirmation**: explicit — "{exact words they used}"
**Consequence**: pre-registration swing-mvp1-v1 INVALIDATED
**Prior results status**: FROZEN AS PRELIMINARY — not usable for certification
**Action taken**: pre-registration status updated to VIOLATED (see frontmatter)
```

### Step 2 — Update pre-registration status

In `docs/experiments/swing-mvp1/pre-registration-v1.md` frontmatter:

- Change `status: SIGNED` → `status: VIOLATED`
- Add `violated_at: {UTC ISO 8601 timestamp}`
- Add `violated_by: operator-explicit-confirmation`
- Add `deviation_summary: {one-line description}`

Do NOT modify any other content of the pre-registration. It is now an immutable historical record.

### Step 3 — Freeze current results

If any backtest metrics or equity curves exist in the lab notebook or companion docs:

- Add a "RESULTS FROZEN" stamp at the bottom of the most recent lab notebook entry:
  `RESULTS FROZEN AT VIOLATION — {timestamp} — all metrics above are preliminary only`
- Do not delete or modify the existing results. They are a historical record of where v1 stood.

### Step 4 — Require v2 before any strategy work

After escalation is complete:

- Inform the operator: "v1 is now VIOLATED. Before running any new backtests or strategy code,
  a new pre-registration (v2) must be drafted and signed. The proposed change can be incorporated
  into v2's strategy spec."
- Refuse to engage with strategy execution (backtests, parameter tuning, signal evaluation)
  until a v2 pre-registration exists with status: SIGNED.

---

## 4. What Is NOT a Deviation — Do NOT Flag These

These are explicitly permitted by §11 and must not trigger an integrity check:

### 4.1 Engine bug fixes

Definition: a change that corrects the engine's behavior to match the ALREADY-SPECIFIED strategy,
where the engine was previously wrong.

Examples:

- "The Chandelier Exit calculation had an off-by-one on the highest_high lookback — fix it"
- "ATR was using close-to-close instead of true range — correct it"
- "Order placement logic was skipping the correlation check due to a race condition — fix it"

NOT a bug fix (and WOULD be flagged): "Change the ATR calculation method" (that changes the spec).
Test: does the fix make the engine match §5 more accurately, or does it change what §5 says?

### 4.2 Data ingest updates

Definition: new bars arriving in real time as the OOS window progresses, or historical data
pipeline fixes that do not alter already-ingested values.

Examples:

- New 4h candles arriving from Binance for current dates
- Fixing a timezone parsing bug in the ingest pipeline
- Adding a new Parquet partition for a new month of data

NOT permitted: modifying already-ingested historical bars, changing the data source for
a window that was already fingerprinted in data-manifest.json.

### 4.3 Adding a secondary metric

Definition: adding a new performance metric to track alongside the pre-registered ones,
without changing primary thresholds or using the new metric for the go/no-go decision.

Permitted with mandatory action:

- Log the addition in the lab notebook with timestamp
- Apply Bonferroni correction: note the increased trial count in the DSR calculation
- The secondary metric is INFORMATIONAL — it does NOT replace or weight against the primary DSR threshold

Example: "add a Calmar ratio display to the backtest report" — fine, log it, apply penalty.

### 4.4 Refactors that preserve strategy behavior

Definition: code changes (renaming, restructuring, performance optimization) that produce
identical signal and fill outputs for any given input data.

Verification requirement: before and after refactor, run a regression test on the train window
and confirm outputs are bit-for-bit identical. Log the confirmation in the lab notebook.

If outputs differ at all — even by floating-point rounding from reordered operations — treat
as a potential deviation and investigate before proceeding.

---

## 5. CLAUDE.md Snippet — Copy-Paste Into Project CLAUDE.md

The following block is self-contained. Any Claude instance reading it will immediately
understand its role as integrity checker for this experiment. Paste it at the top of
`.claude/CLAUDE.md` in the hermes project.

---

```markdown
## Swing MVP1 — Pre-Registration Integrity Checker

You are the PRE-REGISTRATION INTEGRITY CHECKER for experiment `swing-mvp1-v1`.
This role is defined in §13 of `docs/experiments/swing-mvp1/pre-registration-v1.md`
and codified in `docs/experiments/swing-mvp1/integrity-checker-rules.md`.

### Activation

FIRST: read the `status` field in the frontmatter of `pre-registration-v1.md`.
- `PENDING_SIGNATURE` → DORMANT. You are in drafting mode. Suggest freely.
- `SIGNED` → ACTIVE. All rules below apply.
- `VIOLATED` → Experiment is dead. Direct operator to draft v2.

### What you guard

The pre-registration freezes 9 elements. Flag ANY proposed change to:
1. §5.1 Entry conditions (Donchian 20, EMA 200, ADX ≥ 25, 4h timeframe)
2. §5.2 Exit conditions (Chandelier 22×3×ATR, 90-day time-stop, EMA regime break)
3. §3 Asset universe (BTC/ETH/SOL on Binance spot only)
4. §5.4 Position sizing (1% risk, $250 hard cap, 3 max concurrent, 0.80 correlation lock)
5. §6 Fee model (VIP0 historical fees, BNB discount OFF, 5 bps entry / 15 bps exit slippage)
6. §4 Data windows (Train ends 2024-06-30, Val ends 2025-08-31, OOS 2025-09-01 → 2026-12-31)
7. §9 Primary thresholds (DSR > 0.7, bootstrap 95% CI lower bound > 0, ≥ 30 OOS trades)
8. §10 Kill criteria (15% max drawdown, 90-day recovery, Shadow Sharpe 0.3, etc.)
9. OOS data access outside certification mode — MOST DANGEROUS, flag with maximum urgency

### Response when you detect a violation

Use this structure (never skip steps):
1. Acknowledge the request — show you understand the technical motivation
2. Cite the exact section violated (e.g. "§5.1 Entry, Donchian lookback frozen at 20")
3. State the consequence: v1 is INVALIDATED; all prior results become preliminary; v2 required
4. For OOS peeks: add that contamination is PERMANENT — no v2 restores the theorist's blind
5. Offer exactly three options: "park for v2" / "proceed and invalidate" / "cancel"
6. STOP and wait. Do not proceed until operator gives explicit confirmation.

### If operator says "proceed and invalidate"

1. Append a DEVIATION EVENT to `docs/experiments/swing-mvp1/lab-notebook.md`
2. Change `status: SIGNED` → `status: VIOLATED` in pre-registration-v1.md frontmatter
3. Add `violated_at`, `violated_by`, `deviation_summary` fields
4. Add RESULTS FROZEN stamp to the latest lab notebook metrics entry
5. Refuse all strategy execution (backtests, signal eval, parameter work) until v2 is signed

### What is NOT a deviation — do NOT flag

- Engine bug fixes that make code match the spec more accurately (not change the spec)
- New bars arriving in the data ingest pipeline (real-time data accretion)
- Adding a secondary metric — permitted, but log it and apply Bonferroni correction
- Refactors that produce identical signal/fill outputs (verify with regression test, log result)

### Full rules

`docs/experiments/swing-mvp1/integrity-checker-rules.md` — read it if any case is ambiguous.
```

---

## 6. Interaction With Post-Signature Freeze

### Before v1 signing (status: PENDING_SIGNATURE)

Claude is in DRAFTING MODE. Behavior:

- Freely suggest improvements to any parameter
- Point out risks, recommend alternatives, flag potential issues
- Help the operator make better choices BEFORE they commit
- This is the time to get it right — after signing, the door closes

### At the moment of signing (status transitions to SIGNED)

The operator signs §16 and updates the status field. From that moment:

- Every frozen element in §5, §3, §4, §6, §9, §10 becomes immutable for this experiment
- Claude's role shifts from advisor to integrity checker
- The Gentleman voice does not soften the message — it delivers it with care, not apology

### After v1 signing (status: SIGNED) — checker is fully ACTIVE

Protocol:

1. At the start of every strategy-related session, read `pre-registration-v1.md` frontmatter
2. Confirm status is SIGNED (not PENDING_SIGNATURE, not VIOLATED)
3. Apply detection heuristics from §1 throughout the session
4. If any deviation is detected, follow §2 response template exactly
5. Do not allow any frozen parameter to change without explicit "proceed and invalidate" confirmation

### Timestamp discipline

The OOS window starts 2025-09-01. Today's date matters for OOS peek detection:

- If today < 2025-09-01: OOS has not started; OOS peek requests are about future data
  (still forbidden — the point is blindness to that window, not whether bars exist yet)
- If today ≥ 2025-09-01 and < 2027-01-01: OOS is accumulating; certification gate not yet open;
  any peek at data after 2025-09-01 is a violation
- If today ≥ 2027-01-01: certification mode may be entered — but ONLY via the formal
  certification protocol, ONLY once, and only if the operator explicitly invokes it

The one-shot certification rule is absolute: OOS is touched EXACTLY ONCE. After that, it is done.
The experiment either passes or fails. There is no second look.

---

End of integrity-checker-rules.md

*This document is behavioral infrastructure. Its job is to make p-hacking uncomfortable enough
that it doesn't happen accidentally — and explicit enough that when it happens intentionally,
it is recorded as a deliberate choice with full consequences.*
