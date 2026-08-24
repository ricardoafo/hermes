# AGENTS.md — Project-Specific AI Collaboration Protocol

**Type**: Living reference. Any change PRs this file.
**Owners**: Code Owner (sole reviewer, sole approver).
**Applies to**: `ai-bot` (write bot) and any AI agent operating on
this repo via Claude Code, Aider, Cursor, or any similar tool. The Code Owner's
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
or supersedes). Flag the conflict to the Code Owner before acting.

## The three roles

| Role | GitHub identity | What it does | What it never does |
| ----- | -------------- | ------------- | ------------------- |
| **Human** | `ricardoafo` | Decides direction, approves PRs, merges | Runs manual repetitive work when a script exists |
| **Bot** | `ai-bot` | Authors commits, opens PRs, runs CI-adjacent tasks | Merges its own PR, approves any PR, dismisses reviews, force-pushes to main |
| **Agent** | (invoked via Claude Code) | Directs the bot; drafts code, docs, ADRs; runs shell inside sandbox | Bypasses branch protection, edits `.claude/settings.local.json` for its own permissions, deletes user data, calls real-capital APIs without dry-run first |

Naming convention: in prose (docs, PR bodies, commit messages, comments)
the bot is called **`ai-bot`** — a project-neutral alias. The underlying
GitHub account username is operator-owned and only appears where the
literal string is functionally load-bearing: `.github/dependabot.yml`
`assignees`, `git config --local user.name/user.email` commands, and
`gh auth switch --user <name>` invocations. If you see the underlying
GitHub username in a new PR's prose, replace it with `ai-bot`. If you
see `ai-bot` inside a config field that expects a real GitHub username,
the PR broke a functional dependency — flag it, do not merge.

## Absolute prohibitions

These are non-negotiable, apply always, and there is no context in which they
are acceptable. If the user requests one of these, refuse and explain.

1. **Never `git commit --no-verify`**. If a pre-commit hook fails, fix the
   underlying issue. See [dev-workflow.md](docs/guides/dev-workflow.md#for-claude-code--ai-bot).
2. **Never `git push --force` to `main`**. Force-push is only allowed on
   feature branches you own during a rebase workflow before opening a PR.
3. **Never approve or merge a PR you authored**. GitHub blocks this via
   CODEOWNERS + branch protection anyway. Do not attempt to work around it.
4. **Never disable branch protection** except during the one documented
   Emergency Exception (see dev-workflow.md, MVP2+ only, Ricardo only).
5. **Never add AI attribution to commit messages**. No `Co-Authored-By:
   Claude`, no `Generated with...` footers, no emoji signatures. Ever.
6. **Never store secrets in the repo**. Not even encrypted. Not even in
   comments. Not even in test fixtures. Secrets go through
   `pydantic-settings` env-var loading (per ADR-003 §04). Suspected leak
   → stop, alert the Code Owner, do not `git rm` (leaves history — needs
   `git filter-repo` + force-push in the Code Owner's hands).
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
12. **Always open PRs as the `ai-bot`, never as `ricardoafo`**.
    GitHub blocks self-approval (a PR opened by ricardoafo cannot be
    approved by ricardoafo), and our branch protection prevents admin
    bypass. Before `gh pr create`, verify: `gh auth status` shows bot
    is active. If unsure, `gh auth switch --user rafo-claude-bot` first
    (the GH CLI username is the operator-owned account name — the
    project-facing alias is `ai-bot`).
13. **Every git commit in this repo must be authored by the `ai-bot`**.
    The local git config sets `user.name = rafo-claude-bot` (the
    operator-owned account name — the project-facing alias is
    `ai-bot`). Verify: `git config --local --get user.name`. Do NOT use
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
  test function (enforced by `sev1_guard` pytest plugin).
- Never mock the exchange in domain tests. Domain tests use the
  in-memory adapter fake. Adapters have contract tests against
  recorded HTTP fixtures via `syrupy` + `respx` (per ADR-007).

### On code style

- Ruff + mypy (strict) lint on save. Configuration in `pyproject.toml`
  already in place. Follow PEP 8 + PEP 484 by hand for any edge case ruff misses.
- Google-style docstrings on all public API (per ADR-009).
- **No WHAT-comments**. Well-named identifiers explain WHAT. Comments
  are for WHY, and only when the WHY is non-obvious. Per ADR-009.
- Type hints are mandatory on all function signatures and dataclass
  fields. `Any` is a code-review red flag — justify in a comment or
  refactor.

### On CHANGELOG entries

For any PR typed `feat` / `fix` / `refactor` / `perf` (and not scoped
`ci` / `build` / `deps` / `tooling`), the CHANGELOG must be updated.
Two paths satisfy the check:

- **Preferred — add a fragment**: create
  `changelog.d/<pr-number>-<type>.md` where `<type>` is one of
  `added` / `changed` / `deprecated` / `removed` / `fixed` / `security`.
  The file contains a single Markdown bullet describing the change.
  See [`changelog.d/README.md`](changelog.d/README.md) for the naming
  and placeholder conventions. Aggregation into `CHANGELOG.md`
  happens at release cut via
  `scripts/dev/aggregate-changelog.py --apply`.
- **Legacy — edit `CHANGELOG.md` directly** under `[Unreleased]`.
  Still accepted for tiny doc-only PRs, but avoid it for feature/fix
  PRs — every direct edit races with concurrent PRs on the same
  section. Wave 4's fragment pattern exists precisely to eliminate
  that structural conflict.

Rationale: through the #124-#132 queue, EVERY concurrent PR touched
`[Unreleased] → ### Added` and forced a rebase cascade. Fragments
give each PR its own file — no conflict unless two PRs claim the
same PR number (impossible) or the same feature (already a merge
problem elsewhere).

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
- `sdd-design` MUST run any proposed target command(s) against the
  current tree state and report actual output. "Take a position" without
  "verify the position" has caused design misses (e.g., PR #119's
  `--bootstrap` drop that failed CI). This is a phase gate, not a
  nice-to-have.
- `/sdd-verify` validates against Requirements. `CRITICAL` findings
  block merge; `WARNING` and `SUGGESTION` are non-blocking
- **PR body**: use
  `bash scripts/dev/pr-preflight.sh --render-body <change-name>` before
  opening the PR. The render script auto-injects `Closes #N` from
  `issue.md`, populates Summary/Testing/Risk from `change.md`, and
  runs `pr-body-lint.sh` — all in one command. Then open the PR via
  `bash scripts/dev/create-pr.sh --title "<title>" --body-file <rendered>`.
  Do NOT bypass `create-pr.sh` — direct `gh pr create` skips the body
  lint and has caused CI failures on PR #112 and PR #119.
- Post-ship: `change.md` is frozen in the repo per ADR-009 lifecycle
- **Gitleaks discipline for test-only credential-shaped literals**: for
  test files that hold bot tokens, API keys, webhook URLs, JWTs, or any
  other credential-shaped literal (even fake ones), append
  `# gitleaks:allow` inline on the same line as the literal. Ruff's
  `# noqa: S105` silences ruff but does NOT satisfy the "Secrets scan"
  CI job — gitleaks runs separately and scans the raw file content.
  PR #132 (Slack adapter) burned one amend+push cycle on this before
  the pattern was written down.

Full orchestrator contract in `~/.claude/CLAUDE.md` §SDD-Workflow.
Note: existing `openspec/changes/phase-0-python-toolchain/` uses the
prior 3-file convention (proposal.md + spec.md + design.md) and stays
frozen as historical evidence — the 2026-08-22 ADR-009 amendment
introduced `change.md` for future changes.

**Rule 15 — openspec change spec is mandatory for feat/refactor/perf
work** (drift-eval 2026-08-24, closes #194 — enforcement lives at
`scripts/dev/create-pr.sh` + `scripts/dev/pr-preflight.sh`):

Sub-agent implementation tasks that write code MUST be preceded by an
openspec change spec if the task is sized `size:m` or larger. The
orchestrator MUST verify `openspec/changes/<change-name>/change.md`
exists BEFORE delegating any `Write` or `Edit` tool for material work.

Enforcement (both wrappers run the same predicate):

- `scripts/dev/create-pr.sh` — exit 3 when the latest commit's
  conventional-commit type is `feat|refactor|perf` and the branch's
  diff vs `origin/main` includes no `openspec/changes/*/change.md`.
  Bypass: `--skip-openspec-check "reason"` — the reason is required
  and echoed to stderr for audit.
- `scripts/dev/pr-preflight.sh` — same predicate as check #13.
  Bypass: `SKIP_OPENSPEC_CHECK=1 bash scripts/dev/pr-preflight.sh`.

Rationale: between 2026-08-21 and 2026-08-24 the team shipped ~180 PRs
and only 4 openspec change specs landed. The SDD flow was silently
skipped on almost every substantive feature. Two-layer enforcement
turns the rule from a norm into a gate. See
`openspec/changes/dor-dod-enforcement/change.md` for the full design.

Retroactive spec-writing for already-shipped PRs is explicitly out of
scope — forward-looking only.

### On sub-agent delegation

When the orchestrator dispatches work to a sub-agent, the launch
prompt IS the sub-agent's context. Every paragraph the orchestrator
re-states inflates that context window for zero incremental value.
Three rules keep delegations lean:

1. **Reference `openspec/changes/<name>/change.md` by path — never
   paste its content inline.** The sub-agent reads from disk (or the
   `sdd/<name>/change` engram mirror) via `mem_get_observation`.
   Pasting the Design or Decisions section into the launch prompt
   duplicates the source of truth and drifts the moment the file
   is edited.
2. **Sub-agents hydrate artifacts directly from the backend.** The
   orchestrator passes topic keys (`sdd/<name>/spec`, `sdd/<name>/design`,
   `sdd/<name>/apply-progress`) or file paths — not full observation
   content. `mem_search` results are 300-char previews;
   `mem_get_observation` returns the full text.
3. **Every sub-agent launch declares an `estimated_tokens` budget**
   in the launch prompt (rough order-of-magnitude, not a hard cap).
   The orchestrator uses it to spot prompts that ballooned past
   their intent — a delegation that estimates 200k for a task the
   plan sized as 20k is a smell worth investigating before firing.
4. **Fetch origin/main BEFORE spawning a worktree-isolated
   sub-agent.** The orchestrator MUST run `git fetch origin main`
   from the primary tree before creating the worktree or launching
   the sub-agent. Worktrees inherit from local `main`; a stale local
   `main` yields silent conflicts when the sub-agent's PR races
   another merge. One-line cost, prevents multi-PR rebase cascades.
   The 2026-08-22 wave-3 dispatch batch burned two rebases on this
   exact drift — no more.

   **Tooling gate** (Wave 6, 2026-08-23): run
   `bash scripts/dev/launch-agent.sh` from the primary tree BEFORE
   spawning each worktree-isolated sub-agent. The script fetches
   `origin/main`, blocks (exit 1) if local `main` is behind, and
   prints the current `origin/main` SHA on success — pin the
   sub-agent's base against that SHA in the launch prompt so any
   mid-flight drift is diff-able. `--quiet` outputs SHA-only for
   scripting. Rationale + design tradeoffs:
   [`docs/decisions/2026-08-23-fetch-before-launch-tooling.md`](docs/decisions/2026-08-23-fetch-before-launch-tooling.md).
   The prose norm above STAYS — the tool ENFORCES it.

Rationale: a prior apply phase burned ~200k tokens re-describing an
existing `change.md` inline in a sub-agent prompt. The file IS the
spec — the prompt only points at it.

**Sub-agent rate-limit resilience.** A sub-agent that hits a rate
limit, a harness kill, or a network drop mid-flight loses everything
in its context that has not been committed and pushed. The 2026-08-24
Sentry salvage cost one recovery cycle because the sub-agent produced
~200 LOC without ever committing or pushing. Non-trivial sub-agent
prompts MUST include the following checkpoint discipline (see
[`scripts/dev/sub-agent-prompt-checklist.md`](scripts/dev/sub-agent-prompt-checklist.md)
for the copy-paste template):

1. **Commit a WIP checkpoint every ~30 tool uses OR after each
   distinct "Part" completes** (whichever comes first). WIP commits
   use `wip:` prefix — this is the only accepted deviation from
   conventional-commits. They will be squashed or amended into
   conventional-commits form before the final PR opens.
2. **Push the WIP branch to origin** immediately after each WIP
   commit (`git push --force-with-lease` on rewrite). This makes the
   in-flight work salvageable from GitHub if the sub-agent dies —
   the operator does `git fetch origin <wip-branch>` and continues
   from HEAD instead of scraping a stranded worktree.
3. **Save an engram checkpoint (`mem_save`)** after each significant
   milestone: a Part completes, the test suite goes green, the PR
   body is drafted. Topic key: `sdd/<change-name>/apply-progress`.
   Merge with prior progress — do not overwrite (see the
   "Apply-Progress Continuity" rule in `~/.claude/CLAUDE.md`
   §SDD-Workflow).

**Recovery pattern when a sub-agent dies mid-flight** (operator
runbook):

- `git fetch origin <wip-branch>` — pick up whatever was pushed.
- `mem_search` for `sdd/<change-name>/apply-progress` — recover the
  last milestone summary.
- Continue from the last WIP commit. Fix any WIP commits (interactive
  rebase to squash into conventional-commits form) BEFORE opening
  the final PR — conventional-commits pre-commit hook rejects `wip:`
  at commit-msg stage, so a WIP left in place would block the merge.

Applies to: any sub-agent launched to write code across ≥2 files,
implement a Part of a multi-Part spec, or run a chain longer than
~15 tool uses. Trivial one-file mechanical edits do NOT need
checkpoint discipline — the recovery cost is lower than the
overhead.

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

Coverage floor: 90% line, 85% branch. Mutation kill rate is
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
