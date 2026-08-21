# Dev Workflow — Branching, Commits, Pull Requests

**Type**: Guide (living document — PR any change)
**Owners**: Ricardo Afonso (product owner, sole reviewer)
**Applies to**: everyone with write access to `github.com/ricardoafo/hermes`, including
`rafo-claude-bot`.

This guide encodes the version-control discipline for hermes. It is enforced by
GitHub branch protection on `main`, by `.github/CODEOWNERS`, by the pre-commit
hook chain, and by CI checks that land in Phase 0. When this guide and a config
file disagree, the config file wins — open a PR to reconcile the guide.

## Golden rules

1. **No direct pushes to `main`**. Every change lands via pull request. Bootstrap
   commits (before this guide is merged) are the only exception.
2. **Every PR is reviewed and approved by Ricardo Afonso** before merge. No
   self-approval. No bypass. `rafo-claude-bot` cannot approve its own PRs.
3. **Conventional Commits** (see [Commit convention](#commit-convention)) —
   enforced by the `commitizen` pre-commit hook on `commit-msg`.
4. **Squash-merge only** into `main`. Linear history. No merge commits, no
   rebase-merge. The squash commit message is the PR title (which must itself
   be Conventional Commits format).
5. **Feature branches are short-lived**. If a branch lives more than 5 working
   days without a PR opened, delete it or open the PR.
6. **Zero warnings** from any linter, ever. Non-negotiable. See ADR-009 and the
   project rule in engram `preferences/zero-warnings`.

## Branching model

Trunk-based development. One long-lived branch (`main`) plus short-lived
feature branches. No `develop`, no `release/*` branches, no long-running
integration branches.

### Branch naming

| Prefix       | Purpose                                             | Example                            |
| ------------ | --------------------------------------------------- | ---------------------------------- |
| `feat/`      | New capability or user-visible behavior             | `feat/execution-logging`           |
| `fix/`       | Bug fix in existing behavior                        | `fix/fifo-lot-consumption-order`   |
| `docs/`      | Documentation-only change                           | `docs/adr-011-scheduler`           |
| `chore/`     | Tooling, config, dependencies, CI, no user impact   | `chore/renovate-config`            |
| `refactor/`  | Behavior-preserving internal change                 | `refactor/extract-exchange-port`   |
| `test/`      | Test-only additions or fixes                        | `test/hypothesis-fifo-strategies`  |
| `perf/`      | Performance-only change, measured                   | `perf/batch-price-fetch`           |
| `build/`     | Build system, packaging, `pyproject.toml`           | `build/pin-httpx-0.28`             |
| `ci/`        | CI workflow changes                                 | `ci/matrix-python-3.14`            |

Branches use lowercase-kebab-case after the prefix. No issue numbers in branch
names (they belong in the commit body and PR body). No personal prefixes
(`ricardo/*`).

### Who creates branches

- **Ricardo** works on feature branches created from `main`, opens PRs, and
  self-approves via the review workflow (see [Pull requests](#pull-requests)).
- **`rafo-claude-bot`** creates branches only when explicitly directed via a
  Claude Code session. It commits, pushes, opens the PR, and stops. It
  never merges its own PRs.

## Commit convention

[Conventional Commits 1.0.0](https://www.conventionalcommits.org/) — strict.

```text
<type>(<optional-scope>): <description>

<optional-body>

<optional-footer>
```

### Allowed types

| Type       | Meaning                                                                    | CHANGELOG section |
| ---------- | -------------------------------------------------------------------------- | ----------------- |
| `feat`     | New feature, user-visible or API-visible                                   | Added             |
| `fix`      | Bug fix in existing behavior                                               | Fixed             |
| `docs`     | Documentation-only change                                                  | — (not released)  |
| `chore`    | Tooling, deps, CI, meta — no runtime behavior change                       | — (not released)  |
| `refactor` | Behavior-preserving internal change                                        | Changed           |
| `test`     | Test additions or fixes only                                               | — (not released)  |
| `perf`     | Performance improvement, must include measurement in body                  | Changed           |
| `build`    | Build system, packaging                                                    | — (not released)  |
| `ci`       | CI workflow only                                                           | — (not released)  |
| `revert`   | Reverts a prior commit; body must reference the SHA reverted               | Fixed or Removed  |

Any other type (e.g. `wip`, `misc`, `stuff`) is rejected by the commit-msg hook.

### Scope

Optional. When present, matches an ADR area or a hexagonal-arch layer:

- Layer scopes: `domain`, `application`, `adapters`, `cli`, `config`
- Area scopes: `exchange`, `tax`, `fifo`, `notif`, `observability`, `supply-chain`

### Breaking changes

Append `!` after the type/scope and add a `BREAKING CHANGE:` footer explaining
what breaks and how to migrate:

```text
feat(config)!: rename hermes.trade.enabled -> hermes.engine.enabled

BREAKING CHANGE: existing config files under hermes.trade.* must move to
hermes.engine.*. Run `hermes config migrate` to auto-rewrite.
```

Breaking changes drive a MAJOR version bump per ADR-006. Zero tolerance for
silent breakage.

### Prohibited in commit messages

- **AI attribution** — no `Co-Authored-By: Claude`, no `Generated with...`
  footers, no emoji signatures. Ever. Per user rule in
  `~/.claude/CLAUDE.md`. Global instruction override — do not add these even
  if a template suggests them.
- **Issue-tracker links in the subject line** — put them in the footer
  (`Refs: #42`, `Closes: #42`).
- **Multi-paragraph subject lines** — the subject line is one sentence,
  imperative mood, no trailing period, ≤72 chars.

### Commit body

Optional but encouraged for anything non-trivial. Wrap at 72 chars. Explain
**why**, not **what** (the diff is the what). If the change is driven by an
ADR, a Q&A, or an incident, reference it in the body.

## Pull requests

### Opening a PR

- **Base**: `main`.
- **Head**: your feature branch.
- **Title**: Conventional Commits format. This becomes the squash-merge commit
  message. Example: `feat(fifo): implement lot-consumption event log`.
- **Body**: fill the PR template (`.github/pull_request_template.md`) in full.
  Empty or template-only bodies are rejected in review.
- **Draft**: open as Draft when the branch is not yet ready for review. Mark
  Ready for Review only when CI is green and the body is complete.

### PR template sections (mandatory)

1. **Summary** — 1-3 sentences: what changes and why.
2. **Related** — ADR, Q&A, incident, prior PR, or `N/A`.
3. **Change type** — check one: feat / fix / docs / chore / refactor / test
   / perf / build / ci / revert.
4. **Testing** — how the change was verified. For code changes: which tests
   were added or extended, coverage delta if relevant. For docs: `markdownlint
   passes` at minimum.
5. **Zero-warnings compliance** — checkbox confirming all linters pass with
   zero warnings. This is the enforceable version of the global rule.
6. **Risk** — what could break, who feels it first, rollback plan if any.
7. **Follow-ups** — issues opened or planned for out-of-scope items surfaced
   during this PR. Prevents scope creep.

### Reviewers and approvals

- `.github/CODEOWNERS` assigns **@ricardoafo** as reviewer for every path.
  GitHub auto-requests review from him on every PR.
- **Exactly one approval required** — from `@ricardoafo`. No other reviewer
  can unblock a merge.
- **Author cannot approve own PR** — enforced by branch protection setting
  "Require review from Code Owners" + "Dismiss stale approvals".
- **`rafo-claude-bot` PRs** — same rule. Bot opens, Ricardo reviews, Ricardo
  merges (or asks bot to merge post-approval via Claude Code session).

### CI checks (required, land in Phase 0)

Once Phase 0 CI is wired, the following must pass before a PR can merge:

- Pre-commit hooks (markdownlint, ruff, mypy, conventional-commits,
  detect-secrets, trailing-whitespace, end-of-file-fixer)
- Unit + integration tests (pytest)
- Coverage thresholds per ADR-007 (domain ≥95%, application ≥90%, adapters
  ≥80-90%)
- Supply-chain checks (safety, pip-audit) per ADR-010
- CHANGELOG update check per ADR-006 (for `feat` / `fix` / `refactor` / `perf`
  / breaking commits)

Before Phase 0, only the markdown pre-commit hook runs. That is enough for
docs-only PRs, which is all we ship in this bootstrap window.

## Merge policy

### Squash-merge only

- **GitHub merge button config**: allow squash-merge; disallow merge-commit
  and rebase-merge. Configured via repo settings (`gh repo edit`).
- **Squash commit message**: the PR title. Ricardo edits the collapsed body if
  needed before clicking Confirm squash.
- **Result**: `main` has one commit per PR, each in Conventional Commits format,
  each traceable back to its PR via the auto-appended `(#PR-number)` suffix.

### Auto-delete branches after merge

Enabled at the repo level. Feature branches disappear as soon as their PR
merges. This keeps `git branch -r` uncluttered and makes stale branches
detectable at a glance.

### No force-push to `main`

Enforced by branch protection. Force-push to feature branches is allowed
(and expected during rebase workflows before opening a PR).

### No `main` history rewrites

Once a commit is on `main`, it stays. Reverts land as new commits (`revert:`
type). This preserves audit trail — critical for a project handling real
capital and legal artifacts (invoices, tax filings).

## Branch protection on `main` (repo settings)

Configured once via `gh api PATCH /repos/ricardoafo/hermes/branches/main/protection`.
See `scripts/dev/apply-branch-protection.sh` (lands in Phase 0). Current rules:

- **Require pull request reviews before merging**: on
- **Required approving review count**: 1
- **Dismiss stale reviews on new commits**: on
- **Require review from Code Owners**: on
- **Restrict who can dismiss reviews**: `ricardoafo` only
- **Require conversation resolution before merging**: on
- **Require linear history**: on (blocks merge-commits, forces squash or rebase)
- **Do not allow bypassing the above settings**: on (applies to admins too)
- **Allow force pushes**: off
- **Allow deletions**: off

Required status checks are added incrementally as CI workflows land in Phase 0.

## Emergency exceptions

There is one exception to "no direct pushes to `main`": production incident
where the operator (Ricardo) is the sole reviewer of a fix he wrote, and every
minute of delay compounds real-capital exposure. In that case:

1. Ricardo temporarily lifts branch protection via `gh api`.
2. Pushes the hotfix directly to `main`.
3. Re-enables branch protection within 10 minutes.
4. Opens a retrospective issue within 24 hours documenting: what broke, what
   the hotfix did, why review was skipped, and whether a followup PR is needed
   to reconcile.

This exception applies to hermes only, and only after live capital is at
risk (Phase 3+). Before then, no exceptions.

## For Claude Code / `rafo-claude-bot`

When operating in a Claude Code session against this repo:

1. **Never merge a PR** — even if Ricardo says "and merge it". Stop after
   push and PR-open. Ask Ricardo to merge from the GitHub UI or via `gh pr
   merge` command he issues himself.
2. **Never approve a PR** — you may not review-approve any PR, including
   Ricardo's. GitHub blocks this via CODEOWNERS + branch protection anyway,
   but the intent is: bot does not exercise review judgment.
3. **Follow Conventional Commits strictly** — commit-msg hook will reject
   malformed messages. Do not `--no-verify`.
4. **Never `--no-verify` any hook** — per global rule
   `~/.claude/CLAUDE.md`. If a hook fails, fix the underlying issue.
5. **PRs must be opened with the template body filled in** — do not open
   a PR with the raw template. Fill Summary, Related, Testing, Risk,
   Follow-ups every time.
6. **Multiple small PRs beat one large PR** — each PR should be reviewable
   in <15 minutes. If a change needs >500 LOC diff (excluding lockfile /
   generated files), split it.

## Rationale — why this shape

- **Trunk-based over git-flow**: solo developer + AI assistant, no release
  train, no parallel maintenance branches. Git-flow's `develop` branch adds
  ceremony without benefit.
- **Squash-merge over merge-commit**: keeps `main` history readable as a
  changelog. Each merge is one logical change. Bisecting is deterministic.
- **Conventional Commits over free-form**: enables mechanical CHANGELOG
  extraction (ADR-006), enables commit-type filtering during release notes,
  gives future-Ricardo a scannable history.
- **Required review from Ricardo over "self-approval OK"**: forces every
  change through a human decision point. Prevents AI-driven momentum from
  landing something the operator did not choose.
- **No bot merges over "bot can merge post-approval"**: keeps the merge
  decision human. If merging feels routine enough to automate, that is a
  signal the change was too small to bother with anyway.

## Related

- [ADR-006 — Versioning strategy](../architecture/adr-006-versioning-strategy.md)
- [ADR-007 — Testing strategy](../architecture/adr-007-testing-strategy.md)
- [ADR-009 — Documentation strategy](../architecture/adr-009-documentation-strategy.md)
- [ADR-010 — Dependency management](../architecture/adr-010-dependency-management-and-supply-chain.md)
- [Global rules — Ricardo's CLAUDE.md](file:///C:/Users/rafon/.claude/CLAUDE.md)
