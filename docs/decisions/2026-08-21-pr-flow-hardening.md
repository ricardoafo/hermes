# PR flow hardening — auto-index, auto-update, bot identity discipline

**Date**: 2026-08-21
**Author**: claude on behalf of Ricardo
**Related**: dev-workflow.md, AGENTS.md, ADR-006, ADR-009

## Decision

Automate three sources of PR-flow friction that surfaced during the
pre-Phase-0 setup: parallel-PR conflicts on shared index files,
manual "Update branch" clicks that break `require_last_push_approval`,
and CHANGELOG check false-positives on infra-scope fixes.

## Context

The pre-Phase-0 setup opened 10+ PRs across ~24 hours. Recurring
friction:

1. **Shared-file conflicts** — 3 parallel PRs each added a row to
   `docs/guides/README.md`. First merge worked, others conflicted.
   Resolution required manual conflict-resolve + re-approval loops.
2. **Merge friction on stale branches** — `require_last_push_approval`
   - `strict: true` on required checks meant every PR needed a fresh
   "Update branch" click after each other PR merged. The click was
   attributed to the operator, invalidating their prior approval.
3. **CHANGELOG check too strict** — `fix(ci):` triggered the
   `feat/fix/refactor/perf requires CHANGELOG` gate even though
   CI infrastructure fixes don't affect user-visible runtime behavior.
4. **Git commit author = operator** — global git config identifies
   commits as Ricardo, so `require_last_push_approval` blocked
   Ricardo from approving bot-opened PRs (he was "the last pusher"
   by author attribution).
5. **Windows credential manager cached operator PAT** — regardless of
   active `gh` user, `git push` used cached PAT, GH recorded operator
   as pusher.

## Alternatives considered

**Loosen branch protection** (remove `require_last_push_approval`
and/or `strict`): would eliminate friction but loses defense against
"approved a week ago, sneaky push after". Rejected — the discipline
is worth automating around.

**Serialize all merges by hand**: works for tiny PR volume, breaks
under Dependabot + parallel bot work. Rejected.

**Give the operator a "bypass" role**: violates the discipline that
even admins go through review. Rejected.

## Rationale

Automate what should never require a human decision:

- **Auto-generated indexes** eliminate the entire class of
  shared-file conflicts. `scripts/dev/regenerate-guides-index.sh`
  scans `docs/guides/*.md`, extracts `**Description**:` field + git
  log dates, regenerates the README table between marker comments.
  Pre-commit hook + CI verification.
- **Auto-update workflow** on push to main updates every open PR via
  the GH API, attributed to `github-actions[bot]` (a system actor
  that does NOT invalidate reviewer approvals).
- **Scope-aware CHANGELOG check** skips `fix(ci)`, `fix(build)`,
  `fix(deps)`, `fix(tooling)` — infra-only scopes that don't affect
  runtime.
- **Local git config for bot identity** + `gh auth setup-git` ensure
  every commit is attributed to the bot from the operator's machine.

## Consequences

**What this locks in**:

- Manual guides README edits are rejected by CI (must run the
  generator)
- Adding a new guide requires a `**Description**:` field in the
  header block
- Local git config in this repo differs from operator's global
  config (documented as required, not exception)
- Merge-queue and auto-update workflows both exist and use
  `github-actions[bot]` as their actor

**What this forecloses**:

- Custom manual formatting of the guides index (e.g., grouping,
  highlighting, out-of-order rows). If we need that, extend the
  generator, don't bypass it.
- Approving your own PRs (structurally impossible via the flow
  we've built — GH + branch protection + author discipline all
  agree)

**What we'll revisit**:

- If the auto-update workflow ever mis-updates a PR (destroys
  work), reconsider `strict: false` on required checks
- If Dependabot's own rebase mechanism proves sufficient for the
  Dependabot PR subset, we might drop pr-auto-update.yml
- Extend the auto-index pattern to `CHANGELOG.md` (auto-generate
  from Conventional Commits + release tags) when manual CHANGELOG
  editing becomes friction. Deferred until Phase 0+ produces
  enough release-worthy commits to matter.

## Related engram entries

- `hermes/feedback/*` — session lessons that motivated this decision
- `hermes/audit/pdp-patterns-survey` — prior conventions studied
