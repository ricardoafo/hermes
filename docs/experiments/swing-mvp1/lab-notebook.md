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
