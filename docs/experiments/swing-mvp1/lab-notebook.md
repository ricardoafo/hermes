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

### 2026-10-05T22:10:32Z | shadow | operator | engine bug fix: the chandelier stop ratchets up while in a position (issue #680, not a deviation)

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

<!-- entry-sha256: 07990a377bfa7722547a9fa06724e46a610601bf05eb4b8cbcbbd97ef70c6260 -->
