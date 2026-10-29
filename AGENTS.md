# AGENTS.md — Project-Specific AI Collaboration Protocol

**Type**: Living reference. Any change PRs this file (`docs(agents):`).
**Owners**: Code Owner (sole reviewer, sole approver).
**Applies to**: `ai-bot` (write bot) and any AI agent working on this repo.
The Code Owner's global rules at `~/.claude/CLAUDE.md` apply first; this file
layers project rules on top and never contradicts them. The reasons behind
the rules are in
[the AGENTS.md rationale entry](docs/decisions/2026-10-18-agents-md-rationale.md).

## Why this file exists

Hermes handles real capital, generates legal artifacts (invoices, tax
filings), and depends on decisions Ricardo has locked into ADRs. AI
assistance is welcome; AI autonomy is not. If a rule here surprises you,
pause and ask before acting.

## Precedence (read in this order)

1. **User's global rules** — `~/.claude/CLAUDE.md`. Always wins.
2. **This file (`AGENTS.md`)**.
3. **The ADRs** in [`docs/architecture/`](docs/architecture/) — the source
   of truth for *what* and *how*.
4. **[`docs/guides/dev-workflow.md`](docs/guides/dev-workflow.md)** —
   branching, commits, PRs, merges.
5. **[`docs/guides/severity-rubric.md`](docs/guides/severity-rubric.md)** —
   S1-S12 security and F1-F12 functional severity rows.

The higher-precedence document wins. Between ADRs, the higher number wins
(later ADRs refine or supersede). Flag any conflict to the Code Owner before
acting.

## The three roles

| Role | GitHub identity | What it does | What it never does |
| ----- | -------------- | ------------- | ------------------- |
| **Human** | `ricardoafo` | Decides direction, approves PRs (the merge queue then merges them) | Runs manual repetitive work when a script exists |
| **Bot** | `ai-bot` | Authors commits, opens PRs, runs CI-adjacent tasks | Merges its own PR, approves any PR, dismisses reviews, force-pushes to main |
| **Agent** | (Claude Code or similar) | Directs the bot; drafts code, docs, ADRs; runs shell in its sandbox | Bypasses branch protection, edits `.claude/settings.local.json`, deletes user data, calls real-capital APIs without a dry run first |

In prose (docs, PR bodies, commit messages, comments) the bot is
**`ai-bot`**. Its real GitHub username appears only where the literal string
is load-bearing: `.github/dependabot.yml` `assignees`, `git config --local
user.name/user.email`, and `gh auth switch --user <name>`. Replace the real
name in new prose; flag `ai-bot` in a config field that needs a real
username.

## Absolute prohibitions

Non-negotiable in every context. If asked to do one, refuse and explain.

1. **Never `git commit --no-verify`.** Fix what the hook found
   ([dev-workflow.md](docs/guides/dev-workflow.md#for-claude-code--ai-bot)).
2. **Never `git push --force` to `main`.** Force-push only your own feature
   branch, before its PR opens.
3. **Never approve or merge a PR you authored.**
4. **Never disable branch protection**, except the documented Emergency
   Exception (dev-workflow.md; MVP2+, Ricardo only).
5. **Never add AI attribution to commit messages**: no `Co-Authored-By:
   Claude`, no `Generated with...` footers, no emoji signatures.
6. **Never store secrets in the repo**, not even encrypted, in comments or
   in test fixtures. Secrets load from env vars through `pydantic-settings`
   (ADR-003 §04). Suspected leak: stop and alert the Code Owner; do not
   `git rm` (history needs `git filter-repo` in the Code Owner's hands).
7. **Never call real-capital APIs from tests or dry-run flows.** Real-money
   order paths need explicit typed confirmation (invariant I-08, ADR-003
   §00). If you can't tell whether a path reaches a real exchange, assume it
   does.
8. **Never generate trade signals from LLMs** (ADR-001, ADR-005). LLMs
   supervise, monitor and assist development. "Make the LLM pick coins" is
   an architectural pivot: stop and open a discussion with Ricardo.
9. **Never edit `.claude/settings.local.json`** to grant yourself
   permissions. Ask Ricardo.
10. **Never modify accepted ADRs** (ADR-009). A change is a new ADR that
    supersedes the old one. Typo fixes, broken links and rule-11 scrubs are
    allowed.
11. **Never name external or unrelated projects, repos, tools, contractors
    or codebases in any hermes artifact** (files, commits, PR and issue
    bodies, comments, workflow, job and script names). Adopt patterns
    generically; provenance lives in engram. Standard open-source tooling
    (`ruff`, `pytest`, `uv`) is fine; internal codenames are not. The one
    carve-out is dated prior-art research: `docs/research/` and accepted
    ADRs may cite public prior art as evidence.
    `scripts/dev/footprint-check.py` enforces it in CI. Full rule:
    [dev-workflow.md § Zero-footprint policy](docs/guides/dev-workflow.md#zero-footprint-policy).
12. **Always open PRs as the `ai-bot`, never as `ricardoafo`.** Check `gh
    auth status` first; switch with `gh auth switch --user rafo-claude-bot`
    (the operator-owned account behind `ai-bot`).
13. **Every commit is authored by the `ai-bot`**: local `user.name =
    rafo-claude-bot`. No `git commit --author=` override back to the
    operator; `require_last_push_approval` would block the merge. See
    [dev-workflow.md § Local git config](docs/guides/dev-workflow.md#local-git-config-bot-identity-is-required).
    `scripts/dev/create-pr.sh` refuses a PR whose `gh` user is the reviewer
    (exit 4; bypass `SKIP_ACTOR_CHECK=1` with a logged reason). Why the
    split: [automation-trust-chain.md](docs/guides/automation-trust-chain.md).

## Behavioral defaults

### On project-tracker hygiene

GitHub Issues is the tracker. No orphan work, no zombie issues.

1. **Issue first.** Never start work without an issue. Create it with
   `bash scripts/dev/create-issue.sh`, never bare `gh issue create`: the
   wrapper requires `--milestone`, `--assignee` and one `type:`, `area:`
   and `size:` label (dry run: `HERMES_ISSUE_DRY_RUN=1`). SDD changes record
   it in `openspec/changes/<name>/issue.md`.
2. **Close on merge.** The PR body has `Closes #N`; `render-pr-body.sh` adds
   it for SDD PRs.
3. **Comment on deferrals** with the reason and the target milestone.
4. **Close superseded work** with a comment pointing at what replaced it.
5. **Audit each session.** Before new work, `gh issue list --state open`
   and spot-check what should be closed; review the whole milestone before a
   phase transition.

### On unclear scope

Do not guess. Read the ADRs. If still unclear, ask Ricardo before opening a
PR.

### On errors from the harness

When the harness denies an action for a safety reason (permissions,
destructive shared-infra changes, push to main), do not try another command
to the same end. Stop, tell Ricardo what you were doing and why, and let him
decide.

### On testing

- New modules in `src/hermes/` need tests at their ADR-007 tier.
- SEV-1 tests carry `# SEV-1: <rubric row>` above the function
  (`sev1_guard` plugin).
- Every new module under `src/hermes/` (not `__init__`, `_private` or
  `ports/`) lands with `tests/unit/**/test_<name>.py` in the same commit
  (`test-file-adjacency` hook, #160).
- Domain tests use the in-memory adapter fake, never a mocked exchange.
  Adapters have contract tests against recorded HTTP fixtures (`syrupy` +
  `respx`, ADR-007).
- Coverage floors and the mutation floor are defined once, in
  [test-tiers.md](docs/guides/test-tiers.md#coverage-floors). Mutation
  testing currently covers `domain/value_objects` and `domain/lots` weekly
  (issue #709 widens it); line coverage alone is not proof of correctness.

### On code style

- Ruff and mypy (strict), configured in `pyproject.toml`.
- Google-style docstrings on all public API (ADR-009).
- **No WHAT-comments.** Comments say WHY, and only when it is non-obvious.
- Type hints on every function signature and dataclass field. `Any` needs a
  justifying comment or a refactor.

### On CHANGELOG entries

A PR typed `feat`, `fix`, `refactor` or `perf` (not scoped `ci`, `build`,
`deps` or `tooling`) updates the changelog with a fragment:
`changelog.d/<issue-number>-<type>.md`, or `changelog.d/+<slug>.<type>.md`
with no issue, where `<type>` is `added`, `changed`, `deprecated`,
`removed`, `fixed` or `security`. One Markdown bullet. Commit it before the
PR opens and never rename it afterwards (a push after approval dismisses
the approval, #420). Naming rules: [changelog.d/README.md](changelog.d/README.md).
Editing `CHANGELOG.md` directly is accepted only for tiny doc-only PRs.

### On the SDD workflow

Hermes keeps one `openspec/changes/<name>/change.md` per change as the
durable record, mirrored to engram (ADR-009 §6). Once the tracking issue
closes, the change moves to `openspec/archive/` (`scripts/dev/openspec-archive.py`).

**Use SDD** for a new external dependency with real trade-offs, an
architectural shift (new port, adapter kind or bounded context), a breaking
API contract, or a change across 3+ hexagonal layers. **Skip it** for bug
fixes (unless they reveal an architectural gap), single-file refactors,
features within the existing architecture, docs-only and CI/tooling changes.
The bar: if a reviewer would ask "why this way?", write the spec; if only
"does this work?", the PR body and tests are enough.

**When SDD applies**:

1. Create `openspec/changes/<name>/change.md` from `TEMPLATE/change.md`
   (`/sdd-new <name>` where the global SDD commands are installed).
2. Create the tracking issue in the same step and record it in `issue.md`
   from `TEMPLATE/issue.md`. Exactly one tracking issue per change.
3. Fill `change.md` in order: Summary, Scope, Decisions, Requirements,
   Design, Test plan, Risks. Mirror it to engram as `sdd/<name>/change` in
   the same commit.
4. Design runs every proposed command against the current tree and reports
   the real output.
5. Apply reads `sdd/<name>/apply-progress` from earlier batches first and
   merges into it.
6. Verify checks against Requirements: `CRITICAL` blocks the merge;
   `WARNING` and `SUGGESTION` do not.
7. Render the body with `bash scripts/dev/pr-preflight.sh --render-body
   <name>` and open the PR with `bash scripts/dev/create-pr.sh --title
   "<title>" --body-file <rendered>`, never bare `gh pr create`.
8. After the merge, `change.md` is frozen.

**Change spec gate** (#194): a `feat`, `refactor` or `perf` PR sized
`size:m` or larger needs an `openspec/changes/<name>/change.md` in its diff,
and the orchestrator checks it exists before delegating any write.
`create-pr.sh` exits 3 without it (bypass `--skip-openspec-check "reason"`);
`pr-preflight.sh` runs the same predicate (bypass `SKIP_OPENSPEC_CHECK=1`).

**Credential-shaped test literals** (tokens, keys, webhook URLs, JWTs, even
fake ones) carry `# gitleaks:allow` on the same line; `# noqa: S105` does
not satisfy the secrets scan.

### On sub-agent delegation

The launch prompt is the sub-agent's whole context; keep it lean.

1. **Reference `openspec/changes/<name>/change.md` by path**; never paste it.
2. **Pass topic keys or paths, not content.** Sub-agents read artifacts
   themselves (`mem_get_observation` returns the full text;
   `mem_search` only previews).
3. **Declare an `estimated_tokens` budget** in every launch prompt.
4. **Run `bash scripts/dev/launch-agent.sh` before each worktree-isolated
   sub-agent.** It fetches `origin/main`, stops if local `main` is behind,
   and prints the SHA to pin the sub-agent's base to
   ([decision](docs/decisions/2026-08-23-fetch-before-launch-tooling.md)).
5. **Carry the test-tier mandate** (#160): cite
   [ai-authored-tests.md](docs/guides/ai-authored-tests.md) and have the
   sub-agent report the tiers touched (the PR body's `Test tiers touched:`
   line; `create-pr.sh` exits 6 without it) and whether tests came before or
   after the code.

A sub-agent that writes code across 2+ files, implements a Part of a spec,
or runs more than ~15 tool uses also keeps checkpoints: a `wip:` commit
pushed to its branch every ~30 tool uses or after each Part, and an engram
`mem_save` to `sdd/<name>/apply-progress` at each milestone. Squash the
`wip:` commits before the PR opens. The copy-paste prompt text and the
recovery runbook are in
[sub-agent-prompt-checklist.md](scripts/dev/sub-agent-prompt-checklist.md).

### On memory (engram)

- Save decisions, discoveries, bug fixes and patterns without being asked
  (protocol in `~/.claude/CLAUDE.md`).
- Every save uses `project: 'hermes'` and a stable `topic_key`.
- Before ending a session, call `mem_session_summary` with Goal,
  Discoveries, Accomplished, Next Steps and Relevant Files.

## Writing tests

Every test you write or change follows
[ai-authored-tests.md](docs/guides/ai-authored-tests.md):

- A docstring naming the invariant it protects.
- Start from the spec, not the code; one test per invariant.
- Pure-logic modules get a property-based test (hypothesis).
- Every mock names the integration test at the seam.
- Assert specific output values, not internal state; one claim per test.
- **Changing a test to fit a code change: stop.** If it should have failed,
  ask before updating it.
- **No xfail on main.** A test exposing a defect ships with its fix; if the
  fix waits, hold the test back and record the gap in
  `tests/integration/scenarios/failure_modes.py` with its issue.

Before submitting: *if I broke the invariant this test protects, would it
fail?* If not, rewrite it. The skills `hermes-break-it`, `hermes-write-tests`
and `hermes-scenario` in `.claude/skills/` walk through this (#847).

## For humans reading this

- This file is enforced by review; `scripts/dev/agents-md-check.py` only
  keeps it short and free of stale ADR ranges.
- To change a rule, open a `docs(agents):` PR explaining what broke or what
  changed.
- If an AI keeps misreading a rule, sharpen the wording here.

## Related

- `~/.claude/CLAUDE.md` — the Code Owner's global rules (memory, SDD
  orchestrator, git safety); outside this repo.
- [dev-workflow.md](docs/guides/dev-workflow.md) and
  [pr-flow.md](docs/guides/pr-flow.md) — git, PR and merge discipline.
- [test-tiers.md](docs/guides/test-tiers.md) — tiers, coverage and mutation
  floors.
- [ai-authored-tests.md](docs/guides/ai-authored-tests.md) — the test
  discipline.
- [docs/architecture/](docs/architecture/) — the ADRs.
- [severity-rubric.md](docs/guides/severity-rubric.md) — bug and PR
  severity.
