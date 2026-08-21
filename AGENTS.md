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
    Typo fixes and broken-link repairs are allowed; content changes are
    not.

## Behavioral defaults

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

Hermes uses engram-backed SDD (proposal → spec → design → tasks →
apply → verify → archive). Details:

- For **any change touching >2 files across >1 layer** or introducing a
  new pattern: start with `/sdd-new <change-name>`. Skip SDD for typos,
  one-line fixes, dependency bumps, doc corrections.
- SDD artifacts live in engram under topic keys like
  `sdd/{change-name}/{phase}`. Full contract in
  `~/.claude/CLAUDE.md` §SDD-Workflow.
- `/sdd-apply` implements tasks. It MUST read `apply-progress` from
  prior batches before starting (merge, don't overwrite).
- `/sdd-verify` validates against spec. `CRITICAL` findings block
  merge; `WARNING` and `SUGGESTION` are non-blocking.

### On memory (engram)

- Save decisions, discoveries, bug fixes, patterns proactively — do not
  wait to be asked. Full protocol in `~/.claude/CLAUDE.md`
  §Engram-Persistent-Memory.
- Every save uses `project: 'hermes'` and a stable `topic_key` for
  evolving topics (e.g., `architecture/exchange-adapter`).
- Before ending a session or saying "done", call
  `mem_session_summary` with Goal / Discoveries / Accomplished / Next
  Steps / Relevant Files.

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
- [`docs/architecture/`](docs/architecture/) — ADRs 001-010 (source of
  truth for architectural WHAT and HOW)
- [`~/.claude/rules/severity-rubric.md`](../.claude/rules/severity-rubric.md)
  — severity classification for bugs and PRs
