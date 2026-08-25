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

_No entries yet. First entry will be logged at v1 pre-registration signing._
