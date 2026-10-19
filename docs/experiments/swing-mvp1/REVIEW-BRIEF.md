# Overnight Research Brief — Paper-Trading Engine

**For operator review session — 2026-08-26 morning.**

While operator slept, 9 background agents completed research and drafts. This brief is the entry point.

---

## What landed (11 new artifacts + 2 modifications)

### Pre-registration & experiment infrastructure

| Artifact | Location | Status |
| ---------- | ---------- | -------- |
| Pre-registration v1 (PENDING SIGNATURE) | `docs/experiments/swing-mvp1/pre-registration-v1.md` | Awaits operator signature |
| Pre-registration v0 (historical) | `docs/experiments/swing-mvp1/pre-registration-v0.md` | Superseded |
| Lab notebook skeleton | `docs/experiments/swing-mvp1/lab-notebook.md` | Empty, append-only, first entry at v1 signing |
| Integrity checker rules + CLAUDE.md snippet | `docs/experiments/swing-mvp1/integrity-checker-rules.md` | Ready to inject |
| Success criteria companion doc | `docs/experiments/swing-mvp1/success-criteria.md` | Needs reconciliation (see §Open items) |
| Post-mortem template | `docs/experiments/swing-mvp1/post-mortem-template.md` | Ready |
| Regime detection notes (MVP1 heuristic) | `docs/experiments/swing-mvp1/regime-detection-notes.md` | Ready |
| Statistical framework + library plan | `docs/experiments/swing-mvp1/statistical-framework.md` | Ready — mlfinlab abandoned, hand-roll DSR/walk-forward/CPCV |
| Data ingest plan (URLs verified live) | `docs/experiments/swing-mvp1/data-ingest-plan.md` | Ready |
| Fee history CSV (reconstructed) | `docs/experiments/swing-mvp1/fee-history.csv` | Ready with 2 TODO items |

### Architecture

| Artifact | Location | Status |
| ---------- | ---------- | -------- |
| SYNTHESIS amendment (SA-9/10 + Spec 8/9) | `docs/research/paper-trading/SYNTHESIS-AMENDMENT.md` | Additive to SYNTHESIS, does not modify it |
| Umbrella ADR-011 paper-trading engine | `docs/architecture/adr-011-paper-trading-engine.md` | Accepted (confirmed 2026-10-03), 22 decisions |
| Codebase spec-readiness audit | `docs/research/paper-trading/spec-readiness.md` | Ready |
| Prior-art research on pre-registration | `docs/research/paper-trading/prior-art-preregistration.md` | Ready |

---

## Material findings that need your attention

### 1. **Zero-fee BTC period on Binance (July 2022 → ~March 2023)**

Fee agent discovered: Binance ran a zero-fee promotion for BTCUSDT spot for ~8 months. Fee = 0 bps maker + 0 bps taker. **ETH/USDT and SOL/USDT were NOT included.** This materially changes backtest fee math for BTC in that window. Fee lookup must be per-pair. Documented in `fee-history.csv`.

### 2. **SOL data starts 2020-09, not 2020-01**

Pre-registration §4 commits to Train window starting 2020-01-01. BTC + ETH data is complete from that date; SOL data only exists from 2020-09. Not fatal — SOL just contributes 8 fewer months to train. Needs annotation in `data-manifest.json` and in the lab notebook first entry.

### 3. **mlfinlab is abandoned** (no Py 3.14 support)

Pre-registration §8 references purged CV + DSR + walk-forward — I assumed we could use López de Prado's `mlfinlab`. It's unmaintained since 2021. We hand-roll ~180 LOC (DSR ~50, walk-forward ~60, CPCV ~120). This does NOT change the methods, only the implementation source. Recommended stack: `arch` + `scipy` + `numpy` (already dependencies or standard).

### 4. **No formal prior art exists for the full pre-registration protocol**

Existing research platforms, crowdsourced-fund tournaments and López de Prado each cover PARTS. Elements with no known precedent:

- Code-enforced OOS wall (engine physically refuses to read OOS outside certification)
- Pre-specified kill criteria in the pre-registration
- Post-kill new-pre-registration requirement
- Integrity checker role with escalation authority
- 6-state certification lifecycle
- SHA256 commitment to the experiment DESIGN document

**Two interpretations**: (a) we're genuinely novel, or (b) we're overengineering. Worth a conversation.

### 5. **SYNTHESIS ↔ pre-registration inconsistency on hard cap**

SYNTHESIS §3 D-12 says "10% cap" per position. Pre-registration v1 §5.4 says "5% cap." ADR-011 followed pre-registration (correct — pre-reg is binding). But SYNTHESIS itself should be corrected for internal consistency.

### 6. **Confirmed foundational blockers**

- `src/hermes/ports/exchange.py` — verified as empty stub (20 LOC, zero methods). Issue #199 real.
- `src/hermes/adapters/clock/__init__.py` — 0 bytes. No `ClockPort` implementations exist anywhere. Spec 4 creates both `SimulatedClock` and `WallClockClock` from scratch.
- `DomainEvent` needs `event_seq` + `payload_sha256` additions — backward-compat break, touches ~10 test files.
- `Lot` aggregate needs `strategy_id` field — bucket-aware from day 1 per SYNTHESIS.

### 7. **Existing code is better than expected**

- `domain/market_data/orderbook.py` (108 LOC, validated) is exactly the shape Spec 2 needs. No adaptation required.
- Kraken adapter already implements `get_ticker()` — SA-9's depeg monitor plugs in with minimal new code.
- SQLite WAL pattern established in `sqlite_ledger.py` + `migration_runner.py` — Specs 6 and 9 extend the same DB.

### 8. **Timeline update: 12 → 14-16 weeks**

Adding Spec 8 (risk-overlay) and Spec 9 (Strategy Development Protocol) extends the roadmap. Milestone G8 (SHADOW_CERTIFIED) must land by ~2026-10-01 to leave 3-month buffer before 2027-01-01 OOS test.

---

## Open items requiring your decision

| # | Item | Recommendation |
| --- | ------ | ---------------- |
| 1 | **Sign pre-registration v1** | Operator confirmed v1 is solid basis (2026-08-26). Ready to sign after items 3/4/7 propagated (done). Next: compute SHA256 + git-commit. |
| 2 | ~~**Docs directory convention**~~ | RESOLVED 2026-08-26: moved to `docs/research/paper-trading/spec-readiness.md` and empty `docs/adr/` removed. |
| 3 | ~~**SYNTHESIS D-12 correction**~~ | RESOLVED 2026-08-26: SYNTHESIS D-12, wired sizing formula (§T-1), Spec 3 `equity_cap_pct` all updated 10% → 5%. ADR-011 D-12/D-13 already aligned. |
| 4 | ~~**Success-criteria doc reconciliation**~~ | RESOLVED 2026-08-26: rewritten for 9-spec structure. New gate order G1-G8, Spec 9 lands as G5 before Spec 5, timeline 14-16w, G8 pinned 2026-10-01. |
| 5 | **Novelty vs overengineering conversation** | Prior-art research showed no formal precedent for full pre-registration protocol. Are we solving a real problem others avoided, or building unnecessary machinery? (Non-blocking — deferred conversation.) |
| 6 | **Zero-fee BTC window** | Backtest engine must apply 0 bps fees for BTC trades July 2022 → March 2023. Confirm this is intended (it's historically accurate) and add to Spec 2 requirements. (Non-blocking.) |
| 7 | ~~**SOL data gap**~~ | RESOLVED 2026-08-26: option (b) — Train uniform 2020-09-01 for all pairs. Pre-reg v1 §4, data-ingest-plan.md, ADR-011 §consistency-checks all updated. |
| 8 | **Fee history TODOs** | Wayback Machine was blocked in agent environment. 2 manual verifications needed: (i) exact end date of zero-fee BTC (approx 2023-03-22), (ii) Q1 2019 tier restructure exact date (affects VIP1+ only, VIP0 unchanged). (Non-blocking.) |

---

## Recommended review order

1. **Read pre-registration v1** (10 min) — is this what you want to sign?
2. **Skim SYNTHESIS-AMENDMENT** (10 min) — accept the 9-spec structure?
3. **Skim ADR-011** — focus on D-21 (Risk Overlay) and D-22 (Strategy Development Protocol), the two novel decisions
4. **Read SPEC-READINESS** (5 min) — is the codebase state as expected?
5. **Decide on 8 open items above**
6. **If green light, sign v1** — I compute SHA256, we git-commit, work begins

Total review time estimate: ~45-60 min for a thorough pass. All artifacts are self-contained; no need to re-read the 10 SA reports unless you want depth on a specific claim.

---

## What I did NOT do (would need your green light)

- Filed no GH issues (issue creation is operator confirmation territory per auto-mode boundaries)
- Amended no PRs (#196 shadow-mode PR still pending SYNTHESIS decision)
- Committed nothing to git
- Signed nothing
- Downloaded no market data (per data-ingest-plan, downloads start after v1 signing)
- Modified no existing ADRs or SYNTHESIS.md (only additive artifacts)

The floor is yours.
