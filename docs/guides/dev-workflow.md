# Dev Workflow — Branching, Commits, Pull Requests

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (product owner, sole reviewer)
**Applies to**: everyone with write access to `github.com/ricardoafo/hermes`, including
the `ai-bot`.
**Description**: Branching, commits, PRs, merge policy, delete-branch safety, zero-footprint policy.

This guide encodes the version-control discipline for hermes. It is enforced by
GitHub branch protection on `main`, by `.github/CODEOWNERS`, by the pre-commit
hook chain, and by CI checks (all live since MVP1 started). When this guide and a config
file disagree, the config file wins — open a PR to reconcile the guide.

## Golden rules

1. **No direct pushes to `main`**. Every change lands via pull request. Bootstrap
   commits (before this guide is merged) are the only exception.
2. **Every PR is reviewed and approved by the Code Owner** before merge. No
   self-approval. No bypass. The `ai-bot` cannot approve its own PRs.
3. **Conventional Commits** (see [Commit convention](#commit-convention)) —
   enforced by the `commitizen` pre-commit hook on `commit-msg`.
4. **Squash-merge only** into `main`. Linear history. No merge commits, no
   rebase-merge. The squash commit message is the PR title (which must itself
   be Conventional Commits format).
5. **Feature branches are short-lived**. If a branch lives more than 5 working
   days without a PR opened, delete it or open the PR.
6. **Zero warnings** from any linter, ever. Non-negotiable. See ADR-009 and the
   project rule in engram `preferences/zero-warnings`.
7. **Zero footprint of external / unrelated projects**. See
   [Zero-footprint policy](#zero-footprint-policy) below. Non-negotiable.

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
| `chore/`     | Tooling, config, dependencies, CI, no user impact   | `chore/dependabot-config`          |
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
- **The `ai-bot`** creates branches only when explicitly directed via a
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

### Local pre-flight (mandatory before push)

Before every `git push`, a fast subset of the suite CI runs on the PR must
pass locally. This is enforced by a `pre-push` git hook: attempting to push
with any of those checks red is blocked at the git level.

**One-time install** (per clone):

```bash
pre-commit install --hook-type pre-push
```

Adds the pre-push hook to `.git/hooks/pre-push`. Combined with the standard
`pre-commit install` (which installs the commit-stage hooks), every git
operation is gated locally before it touches the remote.

**Manual invocation** (when you want to check without pushing):

```bash
bash scripts/dev/pr-preflight.sh              # full suite, mirrors CI
bash scripts/dev/pr-preflight.sh --pre-push   # the subset the hook runs
bash scripts/dev/pr-preflight.sh --body FILE  # also lint a draft PR body
```

**What the hook runs** (`--pre-push`):

1. PR title (HEAD subject) follows the conventional-commit rule
2. `uv run ruff format --check .` and `uv run ruff check .`, full tree
3. `uv run mypy --strict src/hermes`
4. `uv run lint-imports`, the hexagonal boundary contracts
5. `markdownlint-cli2 "**/*.md" "#node_modules" "#.venv"`, full tree
6. Guides index drift check on `docs/guides/README.md`
7. CHANGELOG fragment and openspec change spec, when the commit type needs
   them
8. (Optional with `--body`) `scripts/dev/pr-body-lint.sh` against the file

There is no "base up-to-date" check: the merge queue keeps branches fresh,
so a push from an older base is fine.

**Left to CI** in pre-push mode, because CI runs them on every head:
`pytest tests/unit/` with coverage and the per-package thresholds,
`pip-audit`, `radon` and `vulture`. The manual full run includes them.
This keeps a push to about a minute instead of about five (#416).

If any check fails locally, it will fail on CI. Fix it at push time, not
after a red-CI round-trip.

**Bypass** — do not skip this hook. If you have a genuine reason a
check should not apply to a specific push, fix the check to
understand the exception (e.g., extend `pr-body-lint.sh`), do not
`git push --no-verify`. Skipping the hook defeats the point.

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

The Conventional Commits type prefix in the PR title conveys the change
category — there is no separate `Change type` section in the template.

1. **Summary** — 1-3 sentences: what changes and why.
2. **Related** — ADR, Q&A, incident, prior PR, GitHub issue. Include
   `Closes #N` for auto-close on merge.
3. **Testing** — how the change was verified. Concrete measurements over
   assertions ("what did you measure?" — not "I ran it and it worked").
   For docs: at minimum `markdownlint-cli2 passes with zero warnings across
   N files`. For code: which pytest files, coverage delta, benchmark
   numbers if perf.
4. **Zero-warnings compliance** — a plain assertion sentence (not a
   checkbox — unchecked boxes render as open tasks in the GH UI). All
   applicable linters pass with zero warnings; if a suppression was
   unavoidable, explain in Risk.
5. **Risk** — what could break, who feels it first, rollback plan if any.
6. **Follow-ups** — issues opened or planned for out-of-scope items
   surfaced during this PR. Prevents scope creep.

### Reviewers and approvals

- `.github/CODEOWNERS` assigns **@ricardoafo** as reviewer for every path.
  GitHub auto-requests review from him on every PR.
- **Exactly one approval required** — from `@ricardoafo`. No other reviewer
  can unblock a merge.
- **Author cannot approve own PR** — enforced by branch protection setting
  "Require review from Code Owners" + "Dismiss stale approvals".
- **`ai-bot` PRs** — same rule. Bot opens, Ricardo reviews, Ricardo
  merges (or asks bot to merge post-approval via Claude Code session).

### CI checks (required, all live)

The following must pass before a PR can merge:

- Pre-commit hooks (markdownlint, ruff, mypy, conventional-commits,
  detect-secrets, trailing-whitespace, end-of-file-fixer)
- Unit + integration tests (pytest)
- Coverage thresholds per ADR-007 (domain ≥95%, application ≥90%, adapters
  ≥80-90%)
- Supply-chain checks (safety, pip-audit) per ADR-010
- CHANGELOG update check per ADR-006 (for `feat` / `fix` / `refactor` / `perf`
  / breaking commits)

During the initial bootstrap window (before MVP1 CI was wired), only the markdown
pre-commit hook ran. Full CI has been enforced since then.

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

### Delete the local branch the moment the push lands in GH

**Every push is followed by a local branch delete.** Push completing is
the definite trigger — remote now has your work, safety condition met,
delete now. Deferring the delete to post-merge leaves an orphan branch
sitting in `git branch` for however long review takes, and "clean up
later" reliably becomes "clean up never".

Sequence:

```bash
# 1. Push the branch (upstream tracking gets set)
git push -u origin <feature-branch>

# 2. Return to main
git checkout main

# 3. Delete the local branch
git branch -d <feature-branch>

# 4. Open the PR (--head refers to the REMOTE branch, not local)
gh pr create --head <feature-branch> ...
```

`git branch -d` (safe form) checks that the branch is merged into its
upstream. Since you just pushed, upstream matches local, so `-d` succeeds.
If `-d` refuses, something is wrong — investigate before force-deleting.

### Iterating on a PR (review comments arrive)

Same discipline applies to every iteration. Re-fetch the branch, edit,
push, delete — every cycle:

```bash
git fetch origin <feature-branch>:<feature-branch>
git checkout <feature-branch>
# edit
git push
git checkout main
git branch -d <feature-branch>
```

Never leave a shell state where a stale feature branch sits after its
work is on remote. If you notice a leftover branch, you missed a delete
step in the prior cycle — clean it now.

### After merge — nothing to clean up locally

Because the local branch was deleted in the push step, there is
nothing local to delete after Ricardo merges the PR. Remote branch
auto-deletes (`--delete-branch-on-merge` repo setting). Fast-forward
local main:

```bash
git fetch --prune origin
git pull --ff-only origin main
```

`--prune` removes stale remote-tracking refs so `git branch -a` stays
tidy. That's the whole post-merge cleanup.

### No force-push to `main`

Enforced by branch protection. Force-push to feature branches is allowed
(and expected during rebase workflows before opening a PR).

### Local git config: bot identity is required

The **local** git config for this repo MUST identify commits as the
`ai-bot`, NOT the operator. Reason: branch protection
`require_last_push_approval: true` blocks a reviewer from approving
commits authored by themselves. If commits are authored by the
operator (from global `user.name` = "Ricardo Afonso"), Ricardo
cannot approve his own bot's PR, and the merge deadlocks.

Global `~/.claude/CLAUDE.md` says "NEVER update the git config" —
that applies to GLOBAL config only. Local (per-repo) config is
explicitly required here and set once at repo setup. The literal
`user.name` / `user.email` values below are the underlying,
operator-owned GitHub account for the bot — the `ai-bot` alias used
in prose maps to this identity:

```bash
git config --local user.name "rafo-claude-bot"
git config --local user.email "279133050+rafo-claude-bot@users.noreply.github.com"
```

Verify with `git config --local --get user.name` (should print the
operator-owned bot account name — the project-facing alias is
`ai-bot`).

Also required: `gh auth setup-git --hostname github.com`. Windows
credential manager otherwise caches the operator's PAT and pushes
under Ricardo's identity regardless of the active `gh` user.

### Parallel PRs on shared files

Two open PRs that both modify the same "index" file (e.g., the guides
README index, `CHANGELOG.md`, a `dependabot.yml` deps block) will
conflict when one merges. Two mitigations:

1. **Auto-generated indexes** — the guides README table is auto-built
   from `docs/guides/*.md` by
   `scripts/dev/regenerate-guides-index.sh`, rebuilt on `main`
   after merge. PRs never commit it; pre-commit and CI only check
   that it builds. Same pattern applies to
   `CHANGELOG.md` (extract from Conventional Commits) and other
   append-only artifacts as they become friction. When adding a
   NEW category of index file, generate it — don't hand-maintain.

2. **Serialize merges when unavoidable** — for the rare case where
   parallel PRs unavoidably touch the same non-generated file,
   merge one, then rebase the others locally against the new `main`
   and push. The merge queue revalidates the merged tree on every
   candidate, so a rebase-and-push is the only human action needed.

### Merge queue: revalidate-then-merge

`.github/workflows/merge-queue.yml` is the merge queue. It runs on
CI completion, on human approval, on push to `main`, and hourly. For
each candidate PR that is approved and mergeable, it:

1. Checks out `main` at the exact SHA it resolved
2. Fetches the PR head at the exact SHA that was approved
3. Builds the merge locally with `git merge --no-commit --no-ff`
4. Runs tree-verifying gates on that tree (markdown-lint, secrets scan)
   — skipped when the head already contains `main` (fast-forward) AND
   CI on that head concluded `success`, because the tree is proven
5. Re-asserts freshness (`main` still there, head still approved) then
   calls `pulls.merge` with `sha=testedHead`

**No push to any PR branch happens, ever.** That is the whole design.
`require_last_push_approval: true` + `dismiss_stale_reviews_on_push:
true` become harmless because the merge queue never pushes to the PR.
Approvals survive.

The judgement lives in `tools/merge/decisions.cjs`, unit-tested by
`tools/merge/decisions.test.cjs`. Adding a new refusal rule means
adding a test first, not editing YAML.

To validate a change to `merge-queue.yml` against real open PRs
without merging anything: run it via `workflow_dispatch` with
`dry_run: true`. Every gate runs; `pulls.merge` is skipped.

### No `main` history rewrites

Once a commit is on `main`, it stays. Reverts land as new commits (`revert:`
type). This preserves audit trail — critical for a project handling real
capital and legal artifacts (invoices, tax filings).

## Branch protection on `main` (repo settings)

Configured via `scripts/dev/setup-branch-protection.sh` (idempotent,
uses the **rulesets API**, not classic branch protection). Requires
`ricardoafo` admin auth to apply.

Active rules on `main`:

- **Require pull request reviews before merging**: on
- **Required approving review count**: 1
- **Dismiss stale reviews on new commits**: on
- **Require review from Code Owners**: on
- **Require last push approval**: on (safe under revalidate-then-merge —
  the queue never pushes to the PR branch)
- **Require extra approval for unattributed changes**: on (guards against
  bot identity misconfiguration mid-PR)
- **Require review-thread resolution before merging**: on
- **Allowed merge methods**: `squash` only
- **Required linear history**: on
- **Non-fast-forward pushes blocked**: on (explicit rule; protects against
  history rewrites)
- **Branch deletion blocked**: on (explicit rule)
- **Required status checks**: `CI success` (single aggregator job)
- **Require branches to be up to date before merging (`strict`)**: **off**
  (see next section)

### Why `strict` is off (and why that is safe)

`strict_required_status_checks_policy: true` requires a PR branch to
include the current `main` tip before it can merge. Keeping a branch
current requires a PUSH, and any push invalidates the reviewer's
approval under `require_last_push_approval` — the two settings fight
each other.

With `strict` off, freshness is enforced by the merge queue instead:
`merge-queue.yml` refuses to merge unless it has just checked out
`main` at the exact SHA, fetched the PR head at the exact approved
SHA, built the merge locally, and asserted twice that neither moved.
The tree that lands is provably validated against the current `main`,
without requiring a push to the PR branch.

Trade-off: if a change to the merge-queue workflow itself is broken,
`strict off` gives less "belt and braces" protection. Mitigation: the
merge decisions are unit-tested (`tools/merge/decisions.test.cjs`) and
`merge-queue.yml` supports a `dry_run` dispatch mode for testing
changes against real open PRs without merging.

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
risk (MVP2+, i.e. real money deployed). Before then, no exceptions.

## Zero-footprint policy

Nothing in this repository — files, doc content, commit messages, PR bodies,
issue bodies, comments, workflow names, job names, script names — references
other projects, repositories, tools, contractors, or codebases by name.
Non-negotiable, applied retroactively (editorial scrubs are allowed and
expected as this rule surfaces existing footprint).

### Why

- hermes stands on its own reputation and design decisions
- External-project references leak assumptions about who has access to what,
  invite confused questions from anyone reading the repo, and age poorly as
  those projects evolve or disappear
- A repository handling real capital and legal artifacts must be a
  self-contained audit trail; auditors reading the repo should not need
  context on unrelated systems
- Removing external references before they accumulate is cheaper than
  scrubbing history later (though we do scrub when needed)

### How to adopt patterns from elsewhere without leaving a footprint

1. Save the source reference to engram (internal memory) with enough
   context to retrieve it later. Engram is not a hermes artifact — it is
   the operator's private notebook.
2. Describe the pattern generically in the hermes artifact: what it does,
   why it works, why hermes adopts it.
3. Do NOT name the source repo, tool, author, contractor, or organization
   in any hermes artifact.
4. If pattern-crediting is genuinely necessary (e.g., a well-known public
   library), use its canonical package name only — never a private-repo
   reference.

### What counts as a footprint

- Repo names (`org/repo` or `repo-name`)
- Internal project codenames
- Contractor or vendor names
- File paths that reveal external structure (`/srv/other-project/`)
- Workflow file names lifted verbatim from another repo
- Branch naming conventions unique to another team
- Comment attributions ("adapted from X's foo.py")

### What does NOT count as a footprint

- Standard open-source library names used in imports (`from pydantic import ...`)
- Public tooling names (`ruff`, `mypy`, `pytest`, `uv`) — these are what we depend on
- Public standards (`Conventional Commits`, `Keep a Changelog`, `SemVer`)
- Public specifications and regulations (`DGT V2232-25`, `LOPDGDD`)

### Editorial scrubs

When footprint is discovered, an editorial-scrub PR is opened with type
`docs` or `chore`, explaining the scope. This rule pre-empts the "ADRs are
immutable" rule for the specific case of external-reference removal.

## For Claude Code / `ai-bot`

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
