# AGENTS.md — Project-Specific AI Collaboration Protocol

**Type**: Living reference. Any change PRs this file.
**Owners**: Ricardo Afonso (sole reviewer, sole approver).
**Applies to**: `rafo-claude-bot` (write bot) and any AI agent operating on
this repo via Claude Code, Aider, Cursor, or any similar tool. Ricardo's
global rules at `~/.claude/CLAUDE.md` apply first; this document layers
project-specific rules on top and never contradicts them.

## Why this file exists

Hermes handles real capital, generates legal artifacts (invoices, tax
filings), and depends on decisions Ricardo has explicitly locked into
ADRs 001-010. AI assistance is welcome; AI autonomy is not. This file is
where the boundaries live. If a rule here surprises you (the AI reading
it), pause and ask before acting.

## Precedence (read in this order)

1. **User's global rules** — `~/.claude/CLAUDE.md` (zero-warnings,
   conventional commits, no AI attribution in commits, etc.). Always wins.
2. **This file (`AGENTS.md`)** — project-specific AI collaboration rules.
3. **ADRs 001-010** — architectural decisions. When in doubt about *what*
   or *how*, ADRs are the source of truth.
4. **`docs/guides/dev-workflow.md`** — the git/gh discipline. Mechanical
   rules on branching, commits, PRs, merges.
5. **`~/.claude/rules/severity-rubric.md`** — S1-S12 security + F1-F12
   functional severity rows. Used by `bug-triager` and by every PR that
   references a defect.

If two documents conflict, the higher-precedence one wins. If ADRs conflict
with each other, the higher-numbered ADR wins (later ADRs are refinements
or supersedes). Flag the conflict to Ricardo before acting.

## The three roles

| Role | GitHub identity | What it does | What it never does |
| ----- | -------------- | ------------- | ------------------- |
| **Human** | `ricardoafo` | Decides direction, approves PRs, merges | Runs manual repetitive work when a script exists |
| **Bot** | `rafo-claude-bot` | Authors commits, opens PRs, runs CI-adjacent tasks | Merges its own PR, approves any PR, dismisses reviews, force-pushes to main |
| **Agent** | (invoked via Claude Code) | Directs the bot; drafts code, docs, ADRs; runs shell inside sandbox | Bypasses branch protection, edits `.claude/settings.local.json` for its own permissions, deletes user data, calls real-capital APIs without dry-run first |

## Absolute prohibitions

These are non-negotiable, apply always, and there is no context in which they
are acceptable. If the user requests one of these, refuse and explain.

1. **Never `git commit --no-verify`**. If a pre-commit hook fails, fix the
   underlying issue. See [dev-workflow.md](docs/guides/dev-workflow.md#for-claude-code--rafo-claude-bot).
2. **Never `git push --force` to `main`**. Force-push is only allowed on
   feature branches you own during a rebase workflow before opening a PR.
3. **Never approve or merge a PR you authored**. GitHub blocks this via
   CODEOWNERS + branch protection anyway. Do not attempt to work around it.
4. **Never disable branch protection** except during the one documented
   Emergency Exception (see dev-workflow.md, Phase 3+ only, Ricardo only).
5. **Never add AI attribution to commit messages**. No `Co-Authored-By:
   Claude`, no `Generated with...` footers, no emoji signatures. Ever.
6. **Never store secrets in the repo**. Not even encrypted. Not even in
   comments. Not even in test fixtures. Secrets go through
   `pydantic-settings` env-var loading (per ADR-003 §04). Suspected leak
   → stop, alert Ricardo, do not `git rm` (leaves history — needs
   `git filter-repo` + force-push in Ricardo's hands).
7. **Never call real-capital APIs from tests or dry-run flows**. All
   real-money order paths require explicit typed confirmation (per
   invariant I-08 in ADR-003 §00). If you can't tell whether a code
   path could reach a real exchange, treat it as if it can.
8. **Never generate trade signals from LLMs**. Per ADR-001 + ADR-005.
   LLMs supervise, monitor, and assist development. LLMs do not decide
   what to trade. If a task looks like "make the LLM pick coins", stop
   and open a discussion with Ricardo — this is an architectural pivot,
   not a coding task.
9. **Never edit `.claude/settings.local.json`** to grant the agent its own
   permissions. The harness enforces this and will deny it — do not try to
   work around the denial. Ask Ricardo to update settings if needed.
10. **Never modify accepted ADRs**. ADRs are immutable once Accepted (per
    ADR-009). A change becomes a new ADR that supersedes the old one.
    Typo fixes, broken-link repairs, and editorial scrubs (see rule 11)
    are allowed; content changes are not.
11. **Never reference external / unrelated projects, repos, tools, contractors,
    or codebases by name in any hermes artifact** — files, doc content,
    commit messages, PR bodies, issue bodies, comments, workflow / job /
    script names. Adopt patterns generically; describe what and why, not
    where the idea came from. Provenance (source repo, author, prior work)
    lives in engram (internal memory), not in hermes. See
    [`docs/guides/dev-workflow.md` §Zero-footprint policy](docs/guides/dev-workflow.md#zero-footprint-policy)
    for the full rule, list of what counts as footprint, and how to
    adopt patterns without leaking references. Standard open-source
    tooling names (`ruff`, `pytest`, `uv`) are not footprint; internal
    project codenames are.
12. **Always open PRs as `rafo-claude-bot`, never as `ricardoafo`**.
    GitHub blocks self-approval (a PR opened by ricardoafo cannot be
    approved by ricardoafo), and our branch protection prevents admin
    bypass. Before `gh pr create`, verify: `gh auth status` shows bot
    is active. If unsure, `gh auth switch --user rafo-claude-bot` first.
13. **Every git commit in this repo must be authored by bot**. The
    local git config sets `user.name = rafo-claude-bot`. Verify:
    `git config --local --get user.name`. Do NOT use
    `git commit --author='...'` overrides that reintroduce the
    operator's identity — `require_last_push_approval` will block the
    merge. See [dev-workflow.md § Local git config: bot identity is
    required](docs/guides/dev-workflow.md#local-git-config-bot-identity-is-required).

## Behavioral defaults

### On project-tracker hygiene

GitHub Issues is the project tracker. Every planned unit of work has an
issue. Every closed issue reflects reality. Every merged PR closes the
issue it addresses. **No orphan work, no zombie issues.**

Five rules (violations degrade the signal value of the board):

1. **File issue first**. Never start work that doesn't have an issue.
   Use the templates in `.github/ISSUE_TEMPLATE/` with proper labels +
   milestone. For SDD changes, `openspec/changes/<name>/issue.md`
   records the link (per [ADR-009 §6](docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)).
2. **Close on merge**. PR body must include `Closes #N` for auto-close.
   SDD PRs get this automatically via `scripts/dev/render-pr-body.sh`.
   Non-SDD PRs: hand-write.
3. **Comment on deferrals**. If an issue moves to a later milestone or
   waits on external state, leave a comment stating the reason + target.
   Don't just leave it stale.
4. **Close superseded work**. If a decision or refactor replaces an
   issue's approach, close it with a comment pointing at the replacing
   PR / decisions-journal entry.
5. **Audit each session**. Before starting new work, `gh issue list
   --state open` + spot-check what should already be closed. Catch drift
   within one session, not weeks later. Before phase transitions, do a
   full milestone review — no zombies carry forward.

Discipline saved in engram at `preferences/gh-project-tracker-discipline`.

### On unclear scope

Do not guess. Read the ADRs. If still unclear, ask Ricardo before opening
a PR. A wasted round-trip is cheaper than a PR that has to be closed.

### On errors from the harness

The harness (Claude Code) denies certain actions with structured messages.
Read the reason carefully. If the reason indicates a safety guardrail
(self-modification of permissions, destructive shared-infra changes,
push-to-main), **do not** try alternative commands to achieve the same
thing. Stop, explain to Ricardo what you were about to do and why, let
him decide.

### On testing

- Every new module in `src/hermes/` needs tests per ADR-007 tier
  (domain ≥95%, application ≥90%, adapters ≥80-90%).
- SEV-1 test paths must be annotated `# SEV-1: <rubric row>` above the
  test function (enforced by `sev1_guard` pytest plugin — lands in
  Phase 0).
- Never mock the exchange in domain tests. Domain tests use the
  in-memory adapter fake. Adapters have contract tests against
  recorded HTTP fixtures via `syrupy` + `respx` (per ADR-007).

### On code style

- Ruff + mypy (strict) lint on save. Configuration in `pyproject.toml`
  lands in Phase 0. Until then, follow PEP 8 + PEP 484 by hand.
- Google-style docstrings on all public API (per ADR-009).
- **No WHAT-comments**. Well-named identifiers explain WHAT. Comments
  are for WHY, and only when the WHY is non-obvious. Per ADR-009.
- Type hints are mandatory on all function signatures and dataclass
  fields. `Any` is a code-review red flag — justify in a comment or
  refactor.

### On the SDD workflow

Hermes uses hybrid SDD: engram for AI working memory + a single
`openspec/changes/{name}/change.md` in the repo for durable audit
trail (per [ADR-009 §6](docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)).

**When to use SDD** (per ADR-009 §6a):

- New external dep with real tradeoffs
- Architectural shift (new port, new adapter kind, new bounded context)
- Breaking API contract
- Cross-cutting change touching 3+ hexagonal layers

**When to SKIP SDD**:

- Bug fixes (unless the fix reveals an architectural gap)
- Single-file refactors
- Features within existing architecture
- Docs-only changes
- CI / tooling / config changes (unless they change the workflow)

The Rust RFC bar: "if a peer reviewer would ask 'why are we doing it
this way?', SDD is worth writing. If they'd only ask 'does this
work?', skip it — the PR body + tests are enough."

**When SDD applies**:

- Start with `/sdd-new <change-name>` — creates
  `openspec/changes/{name}/change.md` from
  `openspec/changes/TEMPLATE/change.md`
- **Create the tracking issue in the same step** — `gh issue create` and
  write the number + URL into `openspec/changes/{name}/issue.md` from
  `openspec/changes/TEMPLATE/issue.md`. Every SDD change has exactly
  one tracking issue (Rust RFC pattern). No exceptions.
- Fill `change.md` sections in order: Summary → Scope → Decisions →
  Requirements → Design → Test plan → Risks
- Mirror to engram under `sdd/{name}/change` in the same commit
- `/sdd-apply` implements from Design. It MUST read `apply-progress`
  from prior batches before starting (merge, don't overwrite)
- `/sdd-verify` validates against Requirements. `CRITICAL` findings
  block merge; `WARNING` and `SUGGESTION` are non-blocking
- **PR body**: use
  `bash scripts/dev/pr-preflight.sh --render-body <change-name>` before
  `gh pr create`. The render script auto-injects `Closes #N` from
  `issue.md`, populates Summary/Testing/Risk from `change.md`, and
  runs `pr-body-lint.sh` — all in one command. Then
  `gh pr create --body-file <rendered>`
- Post-ship: `change.md` is frozen in the repo per ADR-009 lifecycle

Full orchestrator contract in `~/.claude/CLAUDE.md` §SDD-Workflow.
Note: existing `openspec/changes/phase-0-python-toolchain/` uses the
prior 3-file convention (proposal.md + spec.md + design.md) and stays
frozen as historical evidence — the 2026-08-22 ADR-009 amendment
introduced `change.md` for future changes.

### On memory (engram)

- Save decisions, discoveries, bug fixes, patterns proactively — do not
  wait to be asked. Full protocol in `~/.claude/CLAUDE.md`
  §Engram-Persistent-Memory.
- Every save uses `project: 'hermes'` and a stable `topic_key` for
  evolving topics (e.g., `architecture/exchange-adapter`).
- Before ending a session or saying "done", call
  `mem_session_summary` with Goal / Discoveries / Accomplished / Next
  Steps / Relevant Files.

## Writing tests

Every test you author (or modify) MUST follow the discipline in
[`docs/guides/ai-authored-tests.md`](docs/guides/ai-authored-tests.md).
Non-negotiable summary:

- Each test has a docstring naming the invariant it protects.
- Start from the spec, not the code — write one test per invariant,
  then check coverage as a sanity floor.
- For pure-logic modules: add a property-based test (hypothesis).
- Every mock names the integration test at the seam.
- Assert on outputs, not internal state; assert the specific value.
- One behavior claim per test.
- **When updating a test to accommodate a code change: STOP.** If the
  test SHOULD have failed on the behavior change, silencing it defeats
  the point. Ask before updating.

The one question before submitting: *"if I broke the invariant this
test claims to protect, would this test fail?"* If not, rewrite it.

Coverage floor (Phase 0+): 90% line, 85% branch. Mutation kill rate is
the load-bearing gate (target ≥ 80%). Line coverage alone is not proof
of correctness.

## For humans reading this

If you are Ricardo (or a future collaborator with write access):

- This document is enforceable by review only. It is not automated. If a
  PR violates something here, block the merge and reference the section.
- To change a rule, open a PR that edits this file with a
  `docs(agents):` type. Discuss the change in the PR body; explain what
  broke or what new context makes the change necessary.
- If an AI keeps hitting a rule and misinterpreting it, sharpen the
  wording here rather than repeating the correction in every session.

## Related

- [`~/.claude/CLAUDE.md`](../.claude/CLAUDE.md) — user's global rules
  (persistent memory, SDD orchestrator, model assignments, git safety)
- [`docs/guides/dev-workflow.md`](docs/guides/dev-workflow.md) — the
  git/gh discipline this file references
- [`docs/guides/ai-authored-tests.md`](docs/guides/ai-authored-tests.md)
  — mandatory discipline for every test authored in this repo
- [`docs/architecture/`](docs/architecture/) — ADRs 001-010 (source of
  truth for architectural WHAT and HOW)
- [`~/.claude/rules/severity-rubric.md`](../.claude/rules/severity-rubric.md)
  — severity classification for bugs and PRs
