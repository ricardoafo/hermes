# ADR-009: Documentation strategy — ADRs for decisions, dated decisions journal for parameters, runbook for incidents, guides for how-to, docstrings sparingly

**Date**: 2026-08-20
**Status**: Accepted
**Accepted**: 2026-08-21
**Amended**: 2026-08-21 — added §7 (SDD change artifacts, dual-source convention); Neutral bullet on openspec/engram revised to match
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: —

## Context

The project already writes prose in several formats — ADRs (this file
included), research reports, decision notes, engram memory entries. Without a
convention, they will diverge in structure, redundancy will accumulate, and
future-us (or an occasional collaborator) will spend hours reconstructing
what a given piece of documentation is *for*.

The problem with under-specifying documentation is not that we will write
too little; it is that we will write with unclear purpose. A runbook that
reads like an ADR is useless mid-incident. An ADR that reads like a runbook
does not survive the first "why did we choose this?" question.

The counter-problem is over-specification: adopting Sphinx + autodoc +
readthedocs + a full API-reference build pipeline for a solo-operator
system that has no external consumers is process theater. Every doc that
requires infrastructure to publish is a doc that stops being written.

The observation guiding this ADR: **each documentation surface has a
specific reader, a specific question they arrive with, and a specific
lifecycle**. Optimize each surface for its reader; do not blur the surfaces
into one.

## Decision

Documentation lives in **seven surfaces**, each with a clear purpose,
location, and format. Everything else is either engram memory (AI working
context that does not need to persist as a file) or code (which documents
itself if named well).

### The seven surfaces

| Surface           | Location                     | Reader arrives asking                                                            | Lifecycle                                     |
| ----------------- | ---------------------------- | -------------------------------------------------------------------------------- | --------------------------------------------- |
| **ADRs**          | `docs/architecture/`         | Why did we choose X over Y at the architectural level?                           | Immutable once Accepted; new ADR supersedes   |
| **Decisions**     | `docs/decisions/`            | Why is parameter P set to value V? When did that get chosen?                     | Dated entry; parameters change → new entry    |
| **Guides**        | `docs/guides/`               | How do I do task T (setup, dev workflow, release)?                               | Living; edited as tools/workflows change      |
| **Runbook**       | `docs/runbook/`              | Incident I is happening — what do I do right now?                                | Living; each entry per incident type or drill |
| **Research**      | `docs/research/`             | What did we learn before choosing X? What was the evidence?                      | Immutable snapshot; referenced by ADRs        |
| **Change specs**  | `openspec/changes/{name}/`   | What did this SDD change decide, require, and design (proposal / spec / design)? | Frozen once the change ships; then archived   |
| **In-code docs**  | Docstrings + module doc      | What does this function/class do, and when should I use it?                      | Living with the code; deleted with the code   |

### 1. ADRs — Architecture Decision Records

- **Location**: `docs/architecture/` — flat directory of `adr-NNN-slug.md`
  files. Multi-section ADRs use a folder pattern:
  `docs/architecture/adr-NNN-slug/` with `README.md` as the index and
  `NN-section.md` files for the sections (ADR-003 is the model).
- **Format** (established by ADR-001 through ADR-008):
  - `# ADR-NNN: Title — short punch line describing the decision`
  - `**Date**`, `**Status**` (Draft / Proposed / Accepted / Superseded),
    `**Deciders**`, `**Supersedes**` metadata block
  - `## Context` — what forces are at play, what changed to make this a decision
  - `## Decision` — the answer, stated affirmatively
  - `## Why` — reasoning, evidence, alternatives considered
  - `## Consequences` — Positive / Negative / Neutral
  - `## What would change this decision` — the falsifiability clause
  - `## Cross-references` — links to related ADRs and sections
  - `## Pending` — open items required to fully realize the decision
- **Numbering** is sequential; gaps are not renumbered. Superseded ADRs
  stay in place with `**Status**: Superseded by ADR-NNN`; the new ADR links
  back.
- **Immutability rule**: once `Status: Accepted`, an ADR's body is not
  edited except for typo fixes and clarifying language. New decisions get
  new ADRs. The Approval History table in the header records who accepted
  and when.

### 2. Decisions journal — dated small-decision entries

- **Location**: `docs/decisions/` — filename `YYYY-MM-DD-short-slug.md`.
- **Scope**: parameter choices, tool substitutions, threshold changes,
  ad-hoc calls that are not architectural but matter for auditability.
  Examples: "chose 5% rebalance band, not 3%"; "raised tax-guard trade
  count threshold from 400 to 500 after Q1 2027 review"; "switched from
  Yang-Zhang to Rogers-Satchell for realized vol on Nov 2026 stress data".
- **Format**: short — title, date, one paragraph of context, the choice,
  one paragraph of why. Sub-500 words typical. No consequences section;
  those are for ADRs.
- **Cross-reference to ADR**: every decisions-journal entry that touches
  an ADR's assumption cites the ADR: "narrows the recharacterization guard
  from ADR-002".

### 3. Guides — how-to prose for humans

- **Location**: `docs/guides/`.
- **Scope**: reproducible procedures. Setup, dev workflow, release,
  onboarding, environment bootstrap.
- **Format**: task-oriented, imperative voice. Steps numbered. Fenced
  commands runnable as-is (or with obvious substitutions). Screenshots
  only when text cannot do the job.
- **Initial guides planned**:
  - `docs/guides/local-setup.md` — clone → install → run tests
  - `docs/guides/dev-workflow.md` — branching, PR, review, merge
  - `docs/guides/release.md` — cutting a versioned release
  - `docs/guides/shadow-mode-checklist.md` — activating shadow mode before
    real capital
- **Lifecycle**: living. Any procedure change PRs the guide.

### 4. Runbook — incident-response entries

- **Location**: `docs/runbook/`.
- **Scope**: one entry per named incident type or planned drill. When
  something breaks mid-cycle and the operator's phone is buzzing, the
  runbook is the reading material.
- **Format**: title = incident name, then:
  - **Symptom** — what the operator observes
  - **Diagnosis** — how to confirm this is the incident
  - **Immediate action** — the first 5 minutes
  - **Recovery** — the next 60 minutes
  - **Post-incident** — what to write down, what to file
  - **Prevention** — link to relevant ADR / decisions journal / follow-up
- **Initial entries planned**:
  - `docs/runbook/vps-restart-procedure.md`
  - `docs/runbook/failover-to-kraken.md` (per ADR-004)
  - `docs/runbook/ledger-corruption-recovery.md`
  - `docs/runbook/binance-api-key-rotation.md`
  - `docs/runbook/reconciliation-mismatch.md`
  - `docs/runbook/telegram-outage.md`
- **Drill cadence**: quarterly, one runbook per drill; drill results
  become a decisions-journal entry.

### 5. Research — snapshots of investigation

- **Location**: `docs/research/`.
- **Scope**: the writeup of a research investigation whose conclusions
  fed an ADR. Track-1 through Track-7 research reports (per memory)
  live here as `YYYY-MM-DD-track-N-slug.md`.
- **Format**: free-form, but each report begins with an **abstract** of
  ≤ 200 words summarizing the finding + implication. The abstract is
  what the ADR that references it cites.
- **Immutability rule**: research snapshots are dated and immutable
  once merged. Updates get a new report with the newer date; the older
  one remains as the record of what was believed at that time.

### 6. Change specs — SDD artifacts, dual-source convention

- **Location**: `openspec/changes/{change-name}/` — one folder per SDD
  change, following the standard SDD convention. Files inside:
  - `proposal.md` — decisions with rationale (the "what and why" of the change)
  - `spec.md` — testable requirements + acceptance criteria (the "definition of done")
  - `design.md` — file-level shapes and concrete tool config (the "how")
- **Not tracked in this surface**: `explore.md` (throwaway scaffolding
  for the proposal), `tasks.md` (ephemeral implementation breakdown),
  `apply-progress.md` (session state), `verify-report.md` (one-shot CI
  substitute). These live in **engram only**.
- **Dual-source rule**: every artifact in `openspec/changes/` is
  ALSO mirrored to engram under the topic key
  `sdd/{change-name}/{artifact}`. Engram is the AI-side working
  memory (fast retrieval mid-session, survives compaction); the repo
  is the human-side durable audit trail (reviewable in the IDE, on
  GitHub, in PR diffs, in git history). Whenever a phase updates an
  artifact, both sources must be updated in the same commit.
- **Lifecycle**: while a change is open (proposal → apply →
  verify), the folder is live. Once the change ships (squash-merge
  to `main`), the folder is frozen. Post-ship archival strategy
  (leave in place vs move under `openspec/archive/`) is a
  Pending decision.
- **Cross-reference to ADRs**: if a change spec makes an
  architectural decision that will outlive the change, promote the
  content to a new ADR before archiving; the change spec then cites
  the ADR ("architectural rationale codified in ADR-NNN"). Non-
  architectural decisions do NOT need promotion.
- **Readability standards** (change specs are read by humans in PR
  review and by future-them during archaeology — treat them like
  guides, not internal notes):
  - **Headings**: Level 2 for major sections (`## Scope`,
    `## Decisions`, etc.); Level 3 for sub-decisions and
    requirements (`### D-N: <name>`, `### R-N: <title>`); Level 4+
    used sparingly.
  - **Tables**: use them whenever the content is naturally
    columnar (threshold tables, file-purpose maps, verification
    check lists). Padded per invariant I-10.
  - **Fenced code blocks**: language tag mandatory (`toml`,
    `python`, `yaml`, `bash`, `mermaid`). No naked ` ``` `.
  - **Mermaid diagrams**: use where they add value — dependency
    direction, execution sequence, state machine, file-tree
    hierarchy. Do NOT use mermaid for lists that are just as
    readable in bullets. A good test: "does the diagram show a
    relationship the text alone would take a paragraph to
    explain?" If yes, diagram; if no, keep the bullet list.
  - **Executive Summary** at the top of `proposal.md` and
    `design.md`: 3-5 sentences. A reviewer should decide
    whether to read further based on this alone.
  - **Cross-references** to ADRs / sibling artifacts use
    relative markdown links, not bare URLs.

### 7. In-code documentation — docstrings, sparingly

- **Docstring style**: Google-style (numpy-style also acceptable; pick
  one and stick — Google is chosen).
- **Docstring policy**:
  - **Every public module** gets a one-paragraph module docstring
    stating its purpose.
  - **Every public function or class** — if the name is not
    self-explanatory OR the intent is non-obvious, a docstring. If the
    name IS self-explanatory and the signature is unambiguous, no
    docstring is required.
  - **NO WHAT-comments.** `# increment counter\ncounter += 1` is not a
    thing.
  - **YES WHY-comments** on non-obvious business rules, workarounds,
    or invariants: `# WHY: Binance rejects 0-quantity market orders (see ADR-004).`
- **Type hints are mandatory** on every function signature. `mypy`
  strict on domain layer, standard on adapters, allowed-loose on CLI
  glue code.
- **Docstring linter**: `ruff` D-family selectors set to check public
  API only (`D100`, `D101`, `D102`, `D103` on public code). Do not
  enforce `D200`-family (formatting details) — noise for the reader.

### Prose language

- **English throughout**, matching notification and log conventions.
- **Rioplatense Spanish for real-time conversation** with the operator
  (per user preference for CLI assistant interactions) but every
  committed doc is English so it survives collaborator handoff.

### What is NOT written down as a file

- **AI working memory for the current session** — engram instead.
  Session goals, next-steps, "what we discovered in yesterday's
  exploration", scratch task lists, apply-progress logs. Engram is
  optimised for AI retrieval and survives compaction; the repo is
  optimised for human review.
- **Chat transcripts** — engram entries when they contain durable
  learnings; discarded otherwise.
- **AI-generated summaries that duplicate primary artifacts** — a
  Claude-generated "summary of ADR-003" file is redundant; ADR-003
  is the summary.
- **SDD scaffolding artifacts** — `explore.md`, `tasks.md`,
  `apply-progress.md`, `verify-report.md` live in engram only.
  Only the three durable artifacts (`proposal.md`, `spec.md`,
  `design.md`) are mirrored to `openspec/changes/{name}/`. See §6.

### Directory layout

```text
docs/
├── architecture/
│   ├── adr-001-llm-as-supervisor.md
│   ├── adr-002-tax-posture.md
│   ├── adr-003-solution-architecture/
│   │   ├── README.md
│   │   ├── 00-overview-and-design-principles.md
│   │   ├── 01-code-organization.md
│   │   └── ... (through 10)
│   ├── adr-004-exchange-decision.md
│   └── ... (through adr-010)
├── decisions/
│   ├── 2026-08-20-rebalance-band-5-percent.md
│   └── ...
├── guides/
│   ├── local-setup.md
│   ├── dev-workflow.md
│   ├── release.md
│   └── shadow-mode-checklist.md
├── runbook/
│   ├── vps-restart-procedure.md
│   ├── failover-to-kraken.md
│   └── ...
└── research/
    ├── 2026-08-20-track-1-vps.md
    ├── 2026-08-20-track-2-strategy.md
    └── ...
```

## Why

### Why six surfaces and not four (or ten)

Each surface exists because it answers a distinct reader question at a
distinct time. Merging any two loses the distinction:

- ADRs + decisions merged → operational parameter changes require the
  full ADR ceremony, or architectural decisions lose their weight
  (whichever way the merge collapses)
- Runbook + guides merged → mid-incident reader wades through setup
  prose to find the recovery step
- Research + ADRs merged → ADRs bloat with evidence review that
  belongs in a longer analysis document; ADR's `Why` section becomes
  either shallow or unreadable

Six is the number that emerged from writing eight ADRs and needing all
six surfaces already. Fewer would compress; more would fragment.

### Why immutable ADRs

An ADR captures a decision *at the time it was made*. Editing an
accepted ADR to reflect a change loses the record of what was believed
when the code was written. Cross-references break down; the "why did
we do X here" archaeology fails.

Superseding is cheap: `ADR-011: Rebalance-band change from 5% to 4%`
supersedes ADR's parameter choice; the old ADR carries a
`**Status**: Superseded by ADR-011` header and its body remains
readable. This is the same reason SQL migrations are forward-only
(ADR-006).

### Why a decisions journal separate from ADRs

Not every choice is architectural. The rebalance band was 3%, then
5%, based on Aug 2026 backtest data. That is a decision worth
recording — but it is not an ADR-scale question. Turning every
parameter tweak into an ADR would either (a) inflate the ADR count
until they lose signal or (b) discourage tweaks that should happen.

The decisions journal is the pressure valve: short, dated, easy to
write, easy to search.

### Why runbooks are separate from guides

Mid-incident, the reader is stressed. They want the shortest possible
path from symptom to recovery. A guide has context; a runbook has
commands. Merging them means the reader wades through context in the
worst possible reading environment.

The formats reflect this: guides have setup context and rationale;
runbooks have a hard structure (Symptom → Diagnosis → Immediate action
→ Recovery → Post-incident → Prevention) that gets to the fix in
under a page.

### Why "no WHAT-comments" and "yes WHY-comments"

WHAT-comments (`# increment counter`) restate the code. They rot when
the code changes and the comment doesn't; they clutter reading. The
best case is redundancy; the common case is a lie.

WHY-comments (`# Binance rejects 0-quantity orders`) capture what the
next reader needs to know and could not learn from the code alone.
They are cheap to keep accurate because the underlying reality (the
Binance behavior) is stable.

The philosophy alignment with the code: `CLAUDE.md` says "Default to
writing no comments. Only add one when the WHY is non-obvious". This
ADR encodes that rule at the project level.

### Why English throughout for docs

The user is a Spanish-Rioplatense native speaker; conversations
happen in Spanish. Docs are English because:

1. **Collaboration ceiling**. If a Spanish-only collaborator joins,
   English docs still work; if an English-only collaborator joins,
   Spanish-only docs don't.
2. **Tool ecosystem**. Every Python doc, every StackOverflow
   answer, every dependency README is in English. Doc conventions
   in English mesh with the surrounding tooling.
3. **Notifications and logs are English.** Docs matching that
   convention is one less code-switch for the reader.

The operator's real-time conversation with Claude Code is in Spanish;
this ADR does not change that.

## Consequences

### Positive

- **Every doc is easy to place.** "Where does this go?" has an
  answer from the six-surface table.
- **Redundancy is bounded.** Research feeds ADRs but does not
  duplicate them; ADRs feed guides/runbooks but do not duplicate
  them; code feeds docstrings for public API only.
- **ADR immutability protects historical reasoning.** A superseded
  decision is still readable in its original context; new decisions
  are traceable to their predecessor.
- **Runbooks are ready when needed.** Incident-time reading load is
  bounded by the runbook format.

### Negative

- **The initial doc set is larger than a bare-repo would carry.**
  Guides + runbook + decisions journal are three new places for
  content to accumulate. Discipline to keep them lean is the
  operator's cost.
- **Cross-referencing is manual.** No tool checks that "ADR-006
  supersedes ADR-005 parameter X" — the reviewer does. Missed
  cross-references decay documentation over time.
- **English-only docs put a small tax on the Spanish-native
  operator.** Writing prose in a second language is slower even
  at high proficiency. This is accepted; the collaboration ceiling
  benefit outweighs the friction.
- **`ruff` D-family selection is a live decision.** Under-selecting
  misses noise; over-selecting drowns real feedback. The initial
  choice (D100/101/102/103 on public API only) is a starting point
  that may need tuning.

### Neutral

- **No published docs site.** README.md at the repo root is the
  landing page; everything else is accessible in-repo. GitHub renders
  Markdown well enough for casual browsing.
- **CHANGELOG.md lives at the repo root** (per ADR-006 convention),
  not under `docs/`. It is a versioning artifact, not a doc surface.
- **The `openspec/` directory is a doc surface** (§6, added in the
  2026-08-21 amendment). Prior wording (that openspec was "separate
  from docs" and "not a doc surface") is superseded by that
  amendment. Rationale: the human owner needs proposal / spec /
  design accessible in the IDE and on GitHub, not only via the AI's
  engram tools. Engram continues to hold session context and
  scaffolding artifacts (see §5 What is NOT written down).

## What would change this decision

- **Team growth beyond solo.** Guides and runbooks scale to team;
  ADR governance may need explicit reviewer allowlist (already
  covered by claude-bot never self-merging).
- **External consumers appear** (e.g. a plugin API). Public API
  docs need Sphinx or a comparable generator. Additive to this
  ADR; the six surfaces remain.
- **Regulatory requirement for versioned documentation**
  (compliance audit). Immutable ADRs already satisfy most of this;
  runbook drills may need signed logs.
- **Language requirement changes.** If the operator adds
  Spanish-native collaborators without English, prose-language
  becomes bilingual. Same six surfaces, doubled prose cost.

## Cross-references

- [ADR-001](./adr-001-llm-as-supervisor.md) — LLM does not
  generate docs autonomously; every doc is human-authored or
  human-reviewed
- [ADR-003 section 00](./adr-003-solution-architecture/00-overview-and-design-principles.md)
  — invariant I-10 (markdown consistency rules enforced by
  markdownlint)
- [ADR-006](./adr-006-versioning-strategy.md) — CHANGELOG.md
  location; release notes referencing migrations + config schema
- ADR-010 — Dependency management (SBOM + provenance
  documentation may add a new surface) *(pending)*

## Pending

- **`docs/guides/local-setup.md`** — first draft alongside
  Phase 0 scaffolding
- **`docs/runbook/` skeleton** — one seed entry created in
  Phase 0; expanded as incidents happen
- **`docs/decisions/` first entry** — probably the
  rebalance-band choice, once the initial value is chosen
- **`ruff.toml` D-family selection** — set alongside `pyproject.toml`
  in Phase 0
- **Google-style docstring examples** — first module docstring
  landed with the domain module in Phase 1
- **Post-ship archival strategy for `openspec/changes/{name}/`**
  (§6) — leave in place vs move under `openspec/archive/{name}/`
  once the change squash-merges. Pick before the first Phase 0
  change ships.
