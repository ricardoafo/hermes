# Lab Notebook — Swing MVP1 Experiment

**Append-only.** Every entry is a permanent record. Do not delete or reorder entries. Corrections are new entries that reference the erroneous entry, never rewrites.

Format for every entry:

```text
### YYYY-MM-DDTHH:MM:SSZ | phase | actor | action

**Observed metrics** (if applicable):
- metric_name = value

**Decision** (if applicable):
- Decision made

**Rationale**:
- Why this decision was made
- What pre-registration section (if any) governs this
- Whether this counts as a deviation per §11 of pre-registration-v1.md

**Files touched**:
- path/to/file (added | modified | frozen)
```

Phases: `pre-registration` | `data-ingest` | `train` | `validation` | `oos` | `shadow` | `live` | `post-mortem` | `meta`

Actors: `operator` | `claude-experimentalist` | `claude-integrity-checker` | `engine` | `external`

**Anti-p-hacking discipline**: if a metric result triggers a rules deviation, the deviation MUST be logged here BEFORE any code or parameter change. If it isn't logged, the deviation didn't happen — and the change is invalid.

---

## Entries

### 2026-08-25T12:48:18Z | pre-registration | operator | sign pre-registration v1

**Observed metrics**: N/A (pre-experiment)

**Decision**:

- Pre-registration v1 signed as the binding scientific protocol for the Swing MVP1 experiment.
- Content SHA256 anchored to git commit `9ddb879` (pre-signing tree state).
- Verification: `git show 9ddb879:docs/experiments/swing-mvp1/pre-registration-v1.md | sha256sum` → `8d73e99ec658712e2304b872e3231421a5f5eeeafbe8255b199cbb8092db3433`.

**Rationale**:

- Morning review session confirmed pre-reg v1 as solid basis (per operator).
- Three blocking open items resolved before signing:
    1. SYNTHESIS §3 D-12 aligned to pre-reg §5.4 binding 5% cap (was 10%).
    2. Train window uniformised to 2020-09-01 across BTC/ETH/SOL (SOL data availability constraint; option (b) per open-item #7).
    3. Success-criteria doc rewritten for 9-spec structure (Spec 8 = risk-overlay, Spec 9 = Strategy Development Protocol / pre-reg guardrail).
- Non-blocking items 5/6/8 (novelty conversation, zero-fee BTC period, fee-history Wayback TODOs) deferred; do not gate signing.
- Not a deviation per §11 — this is the initial signing event; §11 governs post-signing changes.

**Files touched**:

- `docs/experiments/swing-mvp1/pre-registration-v1.md` — frontmatter status PENDING_SIGNATURE → SIGNED, §16 populated with hash + operator signature + verification protocol.
- `docs/experiments/swing-mvp1/lab-notebook.md` — first entry (this record).
- Prior corrections (pre-signing, in commit `9ddb879`): SYNTHESIS.md, adr-011-paper-trading-engine.md, data-ingest-plan.md, REVIEW-BRIEF.md, success-criteria.md.

**Next**: kick off `/sdd-new spec-1-domain-aggregates` for the first engineering spec via the SDD flow. Data ingest to follow once storage layout is provisioned on the dev D: drive.

### 2026-10-03T13:18:31Z | shadow | operator | operator P&L visibility under a binding no-change rule (not a deviation)

**Observed metrics**:

- N/A (no simulated fill yet; recorded before the 90-day shadow clock starts)

**Decision**:

- Operator console and Telegram pull replies show shadow P&L, gross and after estimated tax.
- No change to pre-registration v1; its file and hash stay as signed.
- Binding rule: no strategy change before certification; halts only by a §10 kill criterion or a logged operational fault; certification is mechanical; AI surfaces stay blind (ADR-012 D-06); v2 cannot reuse the 2025-09-01 to 2026-12-31 window.

**Rationale**:

- Operator commitment, verbatim: "If we commit to not change the strategy on the time period define can we go full?"
- Chosen as "Go full" at 13:18 UTC on 2026-10-03.
- Governing sections: pre-registration v1 §4, §10, §11, §13, §16; D-8 guardrail (2026-10-02 decision).
- Not a deviation per §11: no §11 item changes and the signed file is not edited. Version label if wanted: v1.1, same windows.

**Files touched**:

- docs/decisions/2026-10-03-operator-sees-shadow-pnl.md (added)
- docs/experiments/swing-mvp1/lab-notebook.md (modified)

<!-- entry-sha256: 9d0b2e9efe4eccfc2bdc23b22de599922c2478158e28b54d719cc68a67832f59 -->

### 2026-10-06T11:13:17Z | shadow | operator | shadow clock reset: the run since 2026-10-04 is a warm-up; day 0 waits for the shadow Ready rules (not a deviation)

**Observed metrics**: N/A (no metrics this entry)

**Decision**:

- The shadow run started 2026-10-04T20:13Z (PR #617) does not count toward the §7 90-day shadow: the §7/§10 backtest gate had not run and v1 is still UNTRUSTED (issue #735).
- It keeps running as an engine warm-up. Day 0 is the first bar after every ADR-021 shadow Ready rule holds; the 90 days run from there.
- At day 0 the warm-up ledger is archived read-only and a fresh run starts with an empty ledger and its own run id; the operator runs that step (issue #797).
- The backtest replay starts the test window flat (issue #723) before the gate runs.

**Rationale**:

- Operator words, verbatim: "Current is invalid because it wasnt back tested" and "we will need to kill current shadow later as is invalid" (2026-10-06).
- Governing sections: pre-registration v1 §7 (BACKTEST_CERTIFIED precedes SHADOW_CERTIFIED) and §10 (a failed backtest does not proceed to shadow).
- Not a deviation per §11: no §3, §4, §5, §6 or §9 item changes and the signed file is not edited; this restores the signed order.
- Consequence: certification cannot start before day 0 + 90 days, so the one-shot OOS test moves past 2027-01-01 by however long day 0 lands after 2026-10-03.

**Files touched**:

- docs/architecture/adr-021-strategy-lifecycle-ready-and-done.md (added)
- docs/experiments/swing-mvp1/lab-notebook.md (modified)

<!-- entry-sha256: 0870906765a57d764d7a2180c8fa359c749c9fa8842b450762a8f66f8fe03294 -->

### 2026-10-06T12:32:30Z | shadow | operator | engine bug fix: the chandelier stop ratchets up while in a position (issue #680, not a deviation)

**Observed metrics**:

- In-sample Train window 2020-09-01 to 2024-06-30 only, measured after the decision; base 23892f9 vs the fix
- BTCUSDT: 45 / 45 closed trades, realized PnL -44.69 / -17.56 USDT, daily Sharpe -0.0135 / -0.0053, 33 exits moved (5 earlier, 28 same bar at a higher stop)
- ETHUSDT: 57 / 59 closed trades, realized PnL -208.60 / -193.19 USDT, daily Sharpe -0.0430 / -0.0399, 46 exits moved (7 earlier, 39 same bar at a higher stop)
- SOLUSDT: 56 / 58 closed trades, realized PnL -58.51 / -82.52 USDT, daily Sharpe -0.0071 / -0.0107, 43 exits moved (6 earlier, 37 same bar at a higher stop)
- No data from the sealed 2025-09-01 to 2026-12-31 window was read

**Decision**:

- While a position is open, the stop in force is max(previous stop in force, HH22 - 3 x ATR22). The entry bar and a new position start from the raw level; a restart continues from the position's stored last_stop (stop marks keyed by entry_time).
- The recorded stop_price is the stop in force; chandelier_level stays the raw HH22 - 3 x ATR22. Parameters (22, 3.0), entry rules, sizing, fees and risk rules are unchanged.
- Approved by the operator before the in-sample measurement: ship the ratchet now; the current-bar self-trigger and the close - 1 tick clamp are separate decisions.
- Out of scope, pending the operator: (1) HH22 and ATR22 include the current bar, so a wide up-bar can raise the stop and trigger it on the same bar; (2) the clamp to close - 1 tick described in chandelier.py is not implemented.

**Rationale**:

- Pre-registration v1 §5.3 (pre-registration-v1.md:126): the exit stop is moved as the trailing stop advances. Its source SA-7 (sa7-strategy-design.md:153, :261): the long chandelier stop only moves up, never down. ADR-003 02-data-flow.md:138 says the same. The engine recomputed the raw level every 4h bar, so the stop could fall.
- Integrity rules §4.1 (integrity-checker-rules.md:259-272): an engine bug fix that makes the engine match the already-specified §5 is permitted and is not flagged. This fix matches §5.3; it does not change what §5 says.
- Not a deviation per §11 of pre-registration-v1.md: no §11 item changes and the signed file is not edited. The parameter fingerprint (swing_parameters) is unchanged.
- Backtest parity (ADR-011): the backtest replay drives the same SwingStrategyAdapter bar by bar, so it gets the ratchet with no separate change.

**Files touched**:

- src/hermes/domain/strategy/swing/adapter.py (modified)
- src/hermes/domain/strategy/stop_mark.py (modified, docstring)
- tests/unit/domain/strategy/swing/test_stop_ratchet.py (added)
- tests/unit/domain/strategy/swing/test_stop_record.py (modified)
- tests/integration/scenarios/rules.py (modified)
- tests/e2e/test_serve_cycle.py (modified: the pinned SOL/USDT exit of the 2025-01-19T07:59Z entry moves from 23:59Z to 15:59Z, the raw level fell under the held stop)
- changelog.d/680-fixed.md (added)
- docs/experiments/swing-mvp1/lab-notebook.md (modified)

<!-- entry-sha256: dd4890f09afcedf43be07fb7a438f71f7f2754b4635fb5a63a8372b0c97c6c59 -->

### 2026-10-08T15:16:00Z | validation | operator | Train + Validation backtest gate (issue #735): GATE FAILED, v1 not certified

**Observed metrics**:

- window = 2020-09-01 to 2025-08-31 (Train + Validation), run 2026-10-08T14:38Z at f6883a107d72, clean tree
- dsr = 0.13893 (required > 0.7): FAIL
- max_drawdown = 0.103888 (required < 0.15): PASS
- recovery_days = inf (required < 90 days from any drawdown >= 8%): FAIL
- profit_factor = 0.766709 (required > 1.3): FAIL
- ulcer_index = 7.46858 (required < 10): PASS
- fees_to_gross_pnl = inf, gross P&L <= 0 (required < 0.3): FAIL
- win_rate = 0.324074 (required >= 0.3): PASS
- calibration_fills = 432 (required >= 1): PASS
- hermes certify exit code = 1

**Decision**:

- swing-donchian-4h v1 stays UNTRUSTED. It never gets a shadow day 0 and cannot become the production strategy.
- The v1 shadow on the VPS is invalid as an experiment and is retired once a successor is ready (Code Owner, 2026-10-17).
- Validation 2024-07-01 to 2025-08-31 is spent for the Donchian family. A successor uses a new pre-registration (v2) under the ADR-021 funnel.
- The gate is not re-run on v1 after the issue #785 fix (Code Owner, 2026-10-17); the Train counterfactual puts profit factor near 1.0, still under 1.3.

**Rationale**:

- Pre-registration v1 §9/§10: every metric must pass on Train + Validation before shadow; four fail.
- Warm-up (issue #880): the run starts on the first Train day with no earlier data, so about 2020-09-01 to 2021-03-19 has no entries and about 2021-03-20 to 2021-06-27 decides with 200 to 299 daily bars. Run as signed (Code Owner, 2026-10-07).
- Engine finding after the run (issue #785, Train-only audit 2026-10-12): the trailing stop was raised and tested on the same 4h bar, so 27 of 154 Train exits fired only because of that raise. The fix ships separately as a §11 deviation entry.
- Trial count: this is the only trial in trial-log.jsonl. Three per-pair runs named validate-btc/eth/sol from 2026-08-26 (the day after signing, Phase 1 driver, no trial log) may also have read the Validation window. Their windows were not checked, so Validation is counted as possibly touched up to twice for the Donchian family.
- Not a deviation per §11: no signed rule, window, metric or threshold changes; the outcome is recorded as observed.
- No data from the sealed window (2025-09-01 on) was read.

**Files touched**:

- docs/experiments/swing-mvp1/trial-log.jsonl (added)
- docs/experiments/swing-mvp1/lab-notebook.md (modified)
<!-- entry-sha256: da57b21c57511189b9d23ce8f843e3022a9199c66bb8f96da0357e213a2dabac -->

### 2026-10-17T11:50:44Z | shadow | operator | §11 deviation: the chandelier stop moves once per daily close and a gap under the stop fills at the open (issue #785)

**Observed metrics**:

- Evidence: Train-only harness audit of 2026-10-12 (gate #735), Train 2020-09 to 2024-06; Validation was not read and no data from the sealed 2025-09-01 to 2026-12-31 window was read
- 27 of 154 Train exits fired only because bar t raised the stop with its own high and ATR and then reached it with its own low
- 20 of those exits were booked above that bar's open, a price a resting stop-market cannot fill at
- Counterfactual per pair, 250 notional, no portfolio caps, net PF: current 0.72; stop from bar t-1 with fill min(open, stop) 1.02; stop moved once per daily close 1.01

**Decision**:

- Code Owner decision, 2026-10-17: fix as signed.
- The stop in force during a 4h bar is the ratcheted chandelier level posted at the entry fill or at the latest daily close before the bar; it is never computed from the bar it is tested on, and it only moves up (#680).
- The entry level is posted from the bars closed at the entry fill (the entry bar included, since the market entry fills at its close). A daily close posts HH22 - 3 x ATR22 of the 4h bars closed by then.
- Exit A fires when the bar's low reaches the stop in force. A chandelier exit fills at min(bar open, stop) less the 15 bps stop slippage, in the backtest and in serve's synthetic book alike (one ExecutionModel).
- Time stop, regime break, parameters (22, 3.0), sizing, fees, metrics and kill rules are unchanged.

**Rationale**:

- Pre-registration v1 §5.3: the exit is an on-exchange stop-market at the chandelier level, posted immediately after entry fill and moved daily as the trailing stop advances. The engine recomputed it every 4h bar from that bar, and booked fills above the open.
- Logged as a §11 deviation by the Code Owner's choice: the fix changes the exits the v1 engine produced (§5 as implemented), so it is recorded here before the change, not as a silent engine fix.
- Effect: affects the shadow run and all future backtests. The v1 backtest gate already failed on 2026-10-08 and is not re-run.
- Serve and the backtest share the fix: both drive SwingStrategyAdapter and price stop exits with the same ExecutionModel (ADR-011 parity).

**Files touched**:

- src/hermes/domain/strategy/swing/adapter.py (modified)
- src/hermes/application/execution/prereg_book.py (modified)
- src/hermes/domain/strategy/stop_mark.py (modified, docstring)
- tests/unit/domain/strategy/swing/test_daily_stop.py (added)
- tests/unit/domain/strategy/swing/test_stop_ratchet.py (modified)
- tests/unit/domain/strategy/swing/test_stop_record.py (modified)
- tests/unit/application/execution/test_prereg_book.py (modified)
- tests/unit/application/execution/test_shadow.py (modified)
- tests/unit/application/backtest/test_replay.py (modified)
- tests/integration/scenarios/oracle.py (modified)
- tests/e2e/test_serve_cycle.py (modified) and its status snapshot
- changelog.d/785-fixed.md (added)
- docs/experiments/swing-mvp1/lab-notebook.md (modified)
<!-- entry-sha256: 8cb420aff39d90ca390d428ca3d7f19bb719f0dc574f33cf836c46f103385e49 -->

### 2026-10-17T18:07:43Z | train | claude-experimentalist | first logged v2 Train run (issue #735): GATE FAILED on Train

**Observed metrics**:

- window = 2020-09-01 to 2024-06-30 (Train), backtest workflow run 39056430551 on main at 25c795d, server-01, clean tree
- pre-registration = pre-registration-v2.md, content_sha256 212a109f39b1c31384803d94c842ec550181628883a708deab25eaf2a5535128
- dsr: FAIL
- max_drawdown, recovery_days, profit_factor, ulcer_index, fees_to_gross_pnl, win_rate, calibration_fills: PASS
- funnel pbo, t_stat, positive_years, bear_return, validation_vs_train: FAIL
- funnel trade_count, cost_stress, bull_return, neighbours, correlation: PASS
- hermes certify verdict = GATE FAILED
- trials logged = 8 (base, six §8.5 neighbours, 2x cost stress), all split train

**Decision**:

- swing-mvp1-v2 stays UNTRUSTED.
- Whether to spend a Validation touch on v2 is the Code Owner's call; no Validation run was started.

**Rationale**:

- Pre-registration v2 §10: the backtest gate is judged on Train + Validation; this Train run is development use (§4) and is recorded as observed.
- Values are not recorded here: the AI side sees PASS/FAIL only (ADR-012 D-06). They are in the run's artifact for the Code Owner.
- validation_vs_train cannot pass on a Train-only window: the Validation stretch is empty. It does not change this verdict, since dsr and four other checks fail.
- The workflow's Certify step ended red although certify ran and printed its verdict: the runner shell aborts on certify's exit 1, so the Funnel step was skipped. hermes certify already evaluates every funnel row.
- Not a deviation per §11: no signed rule, window, metric or threshold changes.
- No data from the sealed window (2025-09-01 on) was read.

**Files touched**:

- docs/experiments/swing-mvp1/trial-log.jsonl (modified)
- docs/experiments/swing-mvp1/lab-notebook.md (modified)
<!-- entry-sha256: febf45342a83ab685170de5ded6d8a2c3aebd2453c1cc496125108539750b255 -->

### 2026-10-17T20:56:00Z | train | operator | Code Owner declines a Validation touch for swing-mvp1-v2: v2 is killed at the backtest gate

**Observed metrics**: N/A (no metrics this entry)

**Decision**:

- No Validation run for swing-mvp1-v2. All 3 Validation touches stay unspent.
- swing-mvp1-v2 stays UNTRUSTED and is dead for pre-registration v2; it never reaches shadow.
- A variant needs a new signed pre-registration; its trials join the same trial log and DSR count.
- The post-mortem is due by 2026-10-24.

**Rationale**:

- Pre-registration v2 §10 post-kill protocol: dead for this pre-registration, post-mortem within 7 days, a variant needs a new pre-registration.
- §10 judges the gate on Train + Validation. Train is 46 of the 60 gate months, so a Validation run would be unlikely to lift dsr and the four other failing checks above their thresholds (inferred, not measured). Spending a touch would use up a scarce Validation look for little information.
- Decision taken by the Code Owner on the Backtesting thread decision card (option: skip Validation).
- Not a deviation per §11: no signed rule, window, metric or threshold changes.
- No data from the sealed window (2025-09-01 on) was read.

**Files touched**:

- docs/experiments/swing-mvp1/lab-notebook.md (modified)
<!-- entry-sha256: 8707097409730bc66f3231c2ba6c8612d7a14e58ae10c50f0185ca643184d375 -->
