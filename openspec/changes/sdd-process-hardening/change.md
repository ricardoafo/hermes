# Change: sdd-process-hardening

**SDD change**: `sdd-process-hardening`
**Date**: 2026-08-25
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#214)

Mirror of engram topic key `sdd/sdd-process-hardening/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Codify six process rakes hit during 2026-08-25 autonomous SDD delegation
(PR body drift, PR title casing, missing changelog fragments, author
self-review deadlock, merge-queue design gap under strict policy, and
`action_required` friction on bot-authored CI commits) into permanent
scripted local enforcement plus one architectural reference doc. The
main architectural pick: **extend existing scripts** (`pr-preflight.sh`,
`create-pr.sh`, `changelog-check.sh`, `sub-agent-prompt-checklist.md`)
with mandatory checks rather than introduce new tools; add exactly one
new script (`pr-actor-check.sh`) and one new guide
(`docs/guides/automation-trust-chain.md`). Main risk: over-rejection —
locally-run pre-flight title-lint or actor-check can false-positive
during operator hand-editing, so every new gate ships with a matching
`SKIP_*` env-var escape hatch (pattern established by #194's
`dor-dod-enforcement`). This change dogfoods itself.

## Scope

### In

- **`scripts/dev/pr-actor-check.sh`** — new script. Refuses to open a
  PR when the `gh`-authenticated user matches the intended reviewer.
  Reviewer resolution order: `--reviewer <handle>` arg →
  `HERMES_DEFAULT_REVIEWER` env → hardcoded fallback `ricardoafo`.
  Author read from `gh api user --jq .login` (with `gh auth status`
  parser as fallback). Bypass: `SKIP_ACTOR_CHECK=1`. Self-test matrix
  via `HERMES_SELF_TEST=1`. Exit codes: 0 ok / 1 self-review conflict
  / 2 usage error / 3 gh-auth failure.
- **`scripts/dev/create-pr.sh` gate** — wire `pr-actor-check.sh` into
  the pipeline immediately after the openspec-change gate (added by
  #195). Forward `--reviewer` value; already in `PASSTHROUGH` array.
  Fail with exit code 4 (distinct from openspec exit 3, title 2,
  lint 1) so callers distinguish the failure mode.
- **`scripts/dev/pr-preflight.sh` title check** — new
  `check_pr_title_semantic` that reuses `TITLE_REGEX` from
  `create-pr.sh` (already defined for the CI-parity check). Runs
  against `git log -1 --format=%s HEAD`. Bypass: `SKIP_TITLE_CHECK=1`.
- **`scripts/dev/pr-preflight.sh` mandatory body-lint** — when no
  `--body` or `--render-body` given, auto-derive change name from
  branch (`sdd/<name>` or `feat/<name>` prefix strip), then call
  `render-pr-body.sh <name>` and lint the rendered output. If the
  change name is not derivable AND no explicit body given, fail with
  a message pointing at `--render-body`. Bypass: `SKIP_BODY_CHECK=1`.
- **`scripts/dev/changelog-check.sh` fragment-only mode** — new
  `require-fragment` return in `classify()`. For non-skip scopes
  (`feat|fix|refactor|perf`), require a `changelog.d/*.md` fragment;
  CHANGELOG.md-only edit fails with a message pointing at
  `changelog.d/README.md`. Bypass:
  `ALLOW_CHANGELOG_MD_EDIT=1` (preserved for forks).
- **`scripts/dev/sub-agent-prompt-checklist.md` extension** — append
  items 5-9 to the existing 4-item template:
  5. `isolation: worktree` REQUIRED for parallel sub-agent spawns
  6. PR body via `bash scripts/dev/render-pr-body.sh <change>` only —
     never hand-authored
  7. `changelog.d/wave-<change>.md` fragment is an acceptance
     criterion, not a follow-up
  8. `scripts/dev/no-vague-test-assertions.py` is a pre-PR checkpoint
  9. Run `uv run ruff format .` + `npx markdownlint-cli2 --fix
     "**/*.md"` on the working set BEFORE the first WIP push
- **`docs/guides/automation-trust-chain.md`** — new one-page reference
  tying PR #209 (merge-queue reconciliation for BEHIND under strict
  policy), #210 (auto-update on push:main), and #213 (GitHub App
  auth for both workflows) as the codified hands-off architecture.
  Sections: Problem → Three-workflow architecture → Cross-repo
  reproduction checklist (App install + `vars.HERMES_AUTOMATION_APP_ID`
  - `secrets.HERMES_AUTOMATION_APP_KEY` + workflow patterns) →
  Failure modes (missing App perms, action_required stall). Regenerated
  guides index picks it up via the existing pre-commit hook.
- **`AGENTS.md`** — one paragraph appended under rules 12-13 pointing
  at `pr-actor-check.sh` and the new guide. Keeps the source of truth
  in one place.
- **`changelog.d/wave-sdd-process-hardening.md`** — standard fragment
  following the format established by prior process-hardening waves.

### Out

- **Ruff-in-markdown behavior** — no evidence of recurrence
  post-#213; drop to a docs note under `docs/guides/prose-style.md`
  if it re-surfaces.
- **CI-side title lint removal** — keep as belt-and-suspenders. Local
  pre-flight is an early-catch, not a replacement.
- **`pr-actor-check.sh` in pre-commit hook** — pre-commit is offline,
  `gh` requires network. `create-pr.sh` gate at PR-open time is
  sufficient.
- **Auto-derive reviewer from `CODEOWNERS`** — hardcode `ricardoafo`
  fallback; extend later if a multi-reviewer situation emerges.
- **Retroactive rewrites of existing PRs.**

## Decisions

### D-1: Title check reuses `TITLE_REGEX` from `create-pr.sh`, does not duplicate

- **Decision**: `check_pr_title_semantic` in `pr-preflight.sh` sources
  the `TITLE_REGEX` constant from `create-pr.sh` (or falls back to a
  documented copy with a `# see create-pr.sh:<line>` comment). Runs
  against `git log -1 --format=%s HEAD`.
- **Alternatives**: (a) redeclare regex inline (drifts from CI's
  `subjectPattern`); (b) call `create-pr.sh --title-check-only`
  (couples the two scripts more tightly, adds process fork).
- **Rationale**: Single source of truth for the CI-parity regex.
  Cheapest way to preserve consistency without cross-script
  coupling.
- **Cost of reversal**: LOW — swap in local regex if extraction
  proves fragile.

### D-2: Body-lint auto-renders from openspec change on empty `--body`

- **Decision**: When `pr-preflight.sh` is called without `--body` or
  `--render-body`, derive the change name from the current branch
  (strip common prefixes `sdd/`, `feat/`, `chore/`, `refactor/`,
  `perf/`, `fix/`), verify `openspec/changes/<name>/change.md` +
  `issue.md` exist, then invoke `render-pr-body.sh <name>` and lint
  the result. Fail loud if derivation fails.
- **Alternatives**: (a) hard-require `--body` (breaks operators who
  wrote no body — false negative on the "smooth flow" goal);
  (b) skip body-lint when `--body` absent (current behavior — the
  gap this change closes).
- **Rationale**: Aligns with the openspec-first discipline (#195).
  Every SDD change already has a `change.md`; body-lint should
  leverage that.
- **Cost of reversal**: LOW — remove auto-render, restore
  optional-body behavior via env flag.

### D-3: Changelog fragment mandatory for feat/fix/refactor/perf

- **Decision**: Add `require-fragment` state to
  `classify()`. Non-skip scopes fail if only `CHANGELOG.md` was
  edited; must be a `changelog.d/*.md` fragment.
- **Alternatives**: (a) keep OR-semantics + doc nudge (people ignore
  nudges — verified today); (b) auto-generate a fragment stub
  (bloat + unclear ownership).
- **Rationale**: Fragments avoid merge conflicts on `CHANGELOG.md`
  when parallel PRs land — the exact pattern our new automation
  trust chain (#209/#210/#213) makes possible at scale.
- **Cost of reversal**: LOW — restore OR-semantics via
  `ALLOW_CHANGELOG_MD_EDIT=1`.

### D-4: Actor-check as new script called from `create-pr.sh`

- **Decision**: New script `pr-actor-check.sh` with `HERMES_SELF_TEST=1`
  fixture matrix. Called from `create-pr.sh` after the openspec gate.
- **Alternatives**: (a) inline in `create-pr.sh` (script already
  400+ lines — adds noise); (b) pre-commit hook (offline, no `gh`).
- **Rationale**: Same pattern as `changelog-check.sh` and
  `openspec-change-required.sh` — small dedicated script with
  self-test, wired into `create-pr.sh`. Consistency with existing
  script vocabulary.
- **Cost of reversal**: LOW — un-wire from `create-pr.sh`, script
  becomes unused (dead-code sweeper flags it).

### D-5: Sub-agent template extended, not duplicated

- **Decision**: Append 5 numbered items (5-9) to the existing
  `sub-agent-prompt-checklist.md`. No new file.
- **Alternatives**: (a) new file for orchestrator-side template
  (doubles maintenance); (b) put the extension in AGENTS.md rules
  (bloats prohibitions section — items are checkpoints, not
  prohibitions).
- **Rationale**: The current checklist is the right home. Extension
  keeps it as the single source.
- **Cost of reversal**: LOW — items are numbered, easy to remove.

### D-6: Automation trust chain as a guide, not an ADR

- **Decision**: `docs/guides/automation-trust-chain.md` — reference
  architecture, not a decision that overrides an ADR. Cites the
  inline decisions already under `docs/decisions/2026-08-21-*` and
  `docs/decisions/2026-08-21-revalidate-then-merge.md`.
- **Alternatives**: (a) formal `ADR-013-automation-trust-chain.md`
  (over-formalization for what is essentially a "how to reproduce
  this pattern" doc).
- **Rationale**: The three PRs (#209, #210, #213) each had their
  own inline decision context; the guide is the map, not a new
  decision.
- **Cost of reversal**: LOW — promote to ADR later if the pattern
  is forked to another repo.

## Requirements

Each requirement is a testable statement. Each acceptance criterion is
a shell-verifiable check.

### R-1: PR title semantic check fires locally

- **A-1.1**: `HERMES_SELF_TEST=1 bash scripts/dev/pr-preflight.sh`
  exits 0 (all title fixtures pass).
- **A-1.2**: `HERMES_SELF_TEST=1` fixture — HEAD commit subject
  `feat(x): Telegram foo` (uppercase T after colon) → exit 1;
  stderr contains "lowercase-subject rule".
- **A-1.3**: `SKIP_TITLE_CHECK=1 bash scripts/dev/pr-preflight.sh`
  skips with a warning line, exits 0.

### R-2: Body-lint always runs, auto-rendering when possible

- **A-2.1**: `bash scripts/dev/pr-preflight.sh` on a branch
  matching `sdd/<name>` with a corresponding
  `openspec/changes/<name>/change.md` → auto-renders body, lints
  the result, exits 0 if 4 sections present.
- **A-2.2**: Same as A-2.1 but with a branch name that has no
  derivable change → exits 1 with message pointing at
  `--render-body`; `SKIP_BODY_CHECK=1` skips.
- **A-2.3**: Backward compat — `--body FILE` and `--render-body
  <change>` continue to work exactly as before.

### R-3: Changelog fragment mandatory for user-visible scopes

- **A-3.1**: `HERMES_SELF_TEST=1 bash scripts/dev/changelog-check.sh`
  exits 0 (all new fixtures pass).
- **A-3.2**: Fixture — HEAD commit `feat(x): y` + diff only
  modifies `CHANGELOG.md` → exit 1; stderr points at
  `changelog.d/README.md`.
- **A-3.3**: Fixture — HEAD commit `feat(x): y` + diff adds
  `changelog.d/foo.md` → exit 0.
- **A-3.4**: `ALLOW_CHANGELOG_MD_EDIT=1` restores the OR-semantics.

### R-4: Author self-review blocked at PR-open time

- **A-4.1**: `HERMES_SELF_TEST=1 bash scripts/dev/pr-actor-check.sh`
  exits 0 (fixture matrix passes).
- **A-4.2**: `bash scripts/dev/pr-actor-check.sh --dry-run --author
  ricardoafo --reviewer ricardoafo` exits 1; stderr mentions
  "author cannot self-review".
- **A-4.3**: Same call with `--reviewer rafo-claude-bot` exits 0.
- **A-4.4**: `SKIP_ACTOR_CHECK=1` skips with a warning, exits 0.
- **A-4.5**: `bash scripts/dev/create-pr.sh` invokes
  `pr-actor-check.sh` after the openspec gate — verify via
  `HERMES_SELF_TEST=1 bash scripts/dev/create-pr.sh` fixture where
  a self-review scenario returns exit 4.

### R-5: Sub-agent orchestrator checklist has 5 new items

- **A-5.1**: `grep -c '^[0-9]\+\.' scripts/dev/sub-agent-prompt-checklist.md`
  returns ≥ 9 (was 4).
- **A-5.2**: The new items contain the literal strings `isolation:
  worktree`, `render-pr-body.sh`, `changelog.d/wave-`,
  `no-vague-test-assertions`, and `markdownlint-cli2 --fix`.

### R-6: Automation trust chain guide exists and cites the right PRs

- **A-6.1**: `docs/guides/automation-trust-chain.md` exists.
- **A-6.2**: `grep -c '#20[9]\|#210\|#213'
  docs/guides/automation-trust-chain.md` returns ≥ 3 (all three
  PRs cited by number).
- **A-6.3**: Guide mentions `HERMES_AUTOMATION_APP_ID` and
  `HERMES_AUTOMATION_APP_KEY` verbatim.
- **A-6.4**: `docs/guides/README.md` is regenerated to link the
  new guide (verified by the pre-commit `regenerate-guides-index`
  hook — no manual edit).

## Design

Copy-ready content for the apply phase.

### `scripts/dev/pr-actor-check.sh` (new, ~120 LOC + self-tests)

```bash
#!/usr/bin/env bash
# PR author-vs-reviewer separation gate.
#
# Refuses to open a PR when the gh-authenticated user matches the
# intended reviewer. Enforces the two-account discipline defined in
# AGENTS.md rules 12-13: rafo-claude-bot pushes; ricardoafo reviews.
#
# Called by:
#   * scripts/dev/create-pr.sh — after validate_openspec_change_present,
#     before ensure_body_outside_repo
#   * manually: bash scripts/dev/pr-actor-check.sh --dry-run \
#       --author <login> --reviewer <login>
#
# Env vars:
#   HERMES_DEFAULT_REVIEWER  — fallback reviewer login (default: ricardoafo)
#   SKIP_ACTOR_CHECK         — set to 1 to bypass with a warning
#   HERMES_SELF_TEST         — set to 1 to run fixture matrix + exit
#   HERMES_ACTOR_OVERRIDE    — debugging override for the author read
#
# Exit codes:
#   0 — actor and reviewer differ (ok)
#   1 — actor == reviewer (self-review conflict)
#   2 — usage error
#   3 — gh auth read failed (network or auth missing)

set -euo pipefail

DEFAULT_REVIEWER="${HERMES_DEFAULT_REVIEWER:-ricardoafo}"

# ... [read_author, resolve_reviewer, check, self_test functions] ...
```

### `scripts/dev/pr-preflight.sh` — new `check_pr_title_semantic`

```bash
# After check_openspec_change_required, add:
check_pr_title_semantic() {
    if [[ "${SKIP_TITLE_CHECK:-0}" == "1" ]]; then
        echo "  WARN: title check skipped via SKIP_TITLE_CHECK=1" >&2
        return 0
    fi
    local subject
    subject=$(git log -1 --format=%s HEAD 2>/dev/null || echo "")
    if [[ -z "$subject" ]]; then
        return 0  # empty branch
    fi
    # TITLE_REGEX from create-pr.sh (see line 75) — mirrors CI's
    # amannn/action-semantic-pull-request@v6 subjectPattern.
    if ! [[ "$subject" =~ ^(feat|fix|docs|chore|refactor|test|perf|build|ci|revert)(\(.+\))?(!)?:\ [a-z] ]]; then
        echo "  FAIL: title violates the lowercase-subject rule (CI parity)" >&2
        echo "        subject: ${subject}" >&2
        echo "        expected: 'type(scope): lowercase imperative subject'" >&2
        return 1
    fi
    return 0
}
```

### `scripts/dev/changelog-check.sh` — `require-fragment` state

Add to `classify()` (existing function):

```bash
classify() {
    # ... existing skip-scope logic ...
    case "$type" in
        feat|fix|refactor|perf)
            if [[ "${ALLOW_CHANGELOG_MD_EDIT:-0}" == "1" ]]; then
                echo "either"
            else
                echo "require-fragment"
            fi
            ;;
        # ... other types → skip ...
    esac
}

# In main gate:
verdict=$(classify "$latest_subject")
case "$verdict" in
    require-fragment)
        if ! diff_has_new_file "changelog.d/"; then
            echo "  FAIL: feat/fix/refactor/perf requires a changelog.d/*.md fragment" >&2
            echo "        (CHANGELOG.md-only edits removed per sdd-process-hardening D-3)" >&2
            echo "        See changelog.d/README.md" >&2
            exit 1
        fi
        ;;
    # ... existing branches ...
esac
```

### `scripts/dev/sub-agent-prompt-checklist.md` — appended items

Existing template already has 4 items. Append after item 4:

```markdown
5. **`isolation: worktree` REQUIRED for parallel spawns.** When two or
   more sub-agents run in parallel via the Agent tool, each MUST be
   spawned with `isolation: "worktree"`. Sharing the working tree
   causes `git checkout -b` collisions that surface as stash-corruption
   during recovery (root cause of 2026-08-25's 30-min recovery).

6. **PR body: `render-pr-body.sh <change>` only.** Never hand-author
   the PR body. `bash scripts/dev/render-pr-body.sh <change>` renders
   from `openspec/changes/<change>/change.md` + `issue.md`, auto-injects
   `Closes #N`, and produces the exact shape the `pr-body-lint.sh`
   validator expects.

7. **`changelog.d/wave-<change>.md` is an acceptance criterion.** The
   fragment ships in the same PR as the change; it is not a follow-up.
   The CI `CHANGELOG update` job fails without it (per D-3 of
   sdd-process-hardening).

8. **`scripts/dev/no-vague-test-assertions.py` is a pre-PR checkpoint.**
   Run it against your new tests before the WIP push. Every `assert x
   == y` needs an informative message; the guard rejects bare
   assertions.

9. **Fix-up ONCE before the first WIP push.** Run `uv run ruff format
   .` + `uv run ruff check . --fix` + `npx --no-install
   markdownlint-cli2 --fix "**/*.md"` at the start. Prevents 3-5
   pre-commit-hook fix-loops per commit later (verified in Batch 1/2
   RCA on 2026-08-25).
```

### `docs/guides/automation-trust-chain.md` — new guide

Structure:

```markdown
---
name: Automation trust chain
description: How #209 + #210 + #213 combine to give a hands-off "idea → merge to main" flow
---

# Automation trust chain

This document is the map to the three-workflow architecture that makes
the hermes PR pipeline fully hands-off after human review.

## Problem

<explains: strict_required_status_checks_policy=true + bot-authored
commits + first-time-contributor gates + branch update timing —
the four moving parts that collectively used to require ~5 manual
clicks per PR>

## The three workflows

### 1. `merge-queue.yml` — revalidate-then-merge (PR #209)

<explains merge queue architecture, revalidate-vs-push distinction,
strict policy reconciliation via pulls/{n}/update-branch>

### 2. `auto-update-pr-branches.yml` — push:main freshness (PR #210)

<explains blanket auto-update for all open non-draft PRs on push:main>

### 3. GitHub App authentication (PR #213)

<explains hermes-automation-bot App, why it bypasses action_required,
what secrets/variables are required>

## Cross-repo reproduction checklist

<step-by-step for setting this up in another repo — App creation,
install, secret/var setup, workflow patterns, permission requirements>

## Failure modes

<enumerates: missing App perms, App uninstall, action_required stall
returns, private-key rotation drift>

## Related

- PR #209, PR #210, PR #213 (the trust chain)
- `docs/decisions/2026-08-21-revalidate-then-merge.md`
- `docs/decisions/2026-08-21-pr-flow-hardening.md`
```

### `AGENTS.md` paragraph (appended under rules 12-13)

```markdown
### 13a. Actor separation enforced locally

The bot/human split (rule 12) is enforced at PR-open time by
`scripts/dev/pr-actor-check.sh`, wired into `scripts/dev/create-pr.sh`.
Attempts to open a PR where the `gh`-authenticated user matches the
intended reviewer exit with code 4. Bypass via `SKIP_ACTOR_CHECK=1`
with a logged reason. For the "why" behind the split — and the
automation trust chain that lets ricardoafo review with no manual
CI-approval clicks — see `docs/guides/automation-trust-chain.md`.
```

### `changelog.d/wave-sdd-process-hardening.md`

```markdown
### Added

- **SDD process hardening: codify PR/actor/changelog gates locally**
  (`scripts/dev/pr-actor-check.sh` NEW, `scripts/dev/pr-preflight.sh`,
  `scripts/dev/create-pr.sh`, `scripts/dev/changelog-check.sh`,
  `scripts/dev/sub-agent-prompt-checklist.md`,
  `docs/guides/automation-trust-chain.md` NEW, `AGENTS.md`): permafixes
  six process rakes discovered during 2026-08-25 autonomous SDD
  delegation. (1) `pr-actor-check.sh` refuses PR-open when the
  gh-authenticated user matches the reviewer (silent deadlock previously
  discovered at review time). (2) `pr-preflight.sh` gains local title
  semantic check reusing CI's regex + mandatory body-lint that
  auto-renders from `openspec/changes/<name>/change.md`. (3)
  `changelog-check.sh` requires a `changelog.d/*.md` fragment for
  feat/fix/refactor/perf (CHANGELOG.md-only edit fails; bypass via
  `ALLOW_CHANGELOG_MD_EDIT=1`). (4) Sub-agent checklist extended with
  isolation-worktree requirement, render-pr-body-only rule, changelog
  as acceptance criterion, no-vague-assertions checkpoint, and
  upfront-fix-up rule (all directly caught in Batch 1/2 RCA).
  (5) New guide `automation-trust-chain.md` documents the #209 + #210 +
  #213 hands-off architecture as a cross-repo reproduction reference.
  Every new gate ships with a `SKIP_*` escape hatch per the pattern
  established by `openspec/changes/dor-dod-enforcement/`. Change
  dogfoods itself — its own PR passes every check it introduces.
```

## Test plan

1. Every A-N.M acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` on this branch is all green.
3. `HERMES_SELF_TEST=1 bash scripts/dev/{pr-actor-check,pr-preflight,pr-body-lint,changelog-check,create-pr}.sh` all exit 0.
4. Dogfood: this change's PR opens via `bash scripts/dev/create-pr.sh
   --render-body sdd-process-hardening` and passes the actor check
   (rafo-claude-bot opens, ricardoafo reviews).
5. Manual smoke: throwaway branch attempting self-review → blocked
   with clear error message + escape-hatch hint.

## Risks

- **R1** — Title check over-rejects during rebase (HEAD is a fixup
  commit). Mitigation: `SKIP_TITLE_CHECK=1` documented in
  `pr-preflight.sh --help`.
- **R2** — `gh auth status` output format drift breaks the
  actor-check parser. Mitigation: use `gh api user --jq .login` as
  primary read; self-test covers both parsers.
- **R3** — Windows `gh` (PowerShell) vs bash `gh` may authenticate to
  different accounts due to Windows credential manager caching.
  Mitigation: guide note + `HERMES_ACTOR_OVERRIDE` env for debugging.
- **R4** — Body auto-render requires deriving change name from
  branch. Heuristic (`branch-basename minus 'sdd/' or 'feat/'
  prefix`) misses unconventional names. Mitigation: explicit
  `--body` still supported; `SKIP_BODY_CHECK=1` last-resort.
- **R5** — `require-fragment` breaks in-flight PRs that used
  CHANGELOG.md-only edits under the old OR-semantics. Mitigation:
  `ALLOW_CHANGELOG_MD_EDIT=1` preserves old behavior; migration
  window (until next SDD delegation batch) documented in the
  changelog fragment.

## Dependencies on other changes

- MERGED: PR #209 (merge-queue BEHIND reconciliation), #210
  (auto-update on push:main), #213 (GitHub App auth). The
  automation trust chain guide references these — landing sequence
  matters, all three are prerequisites for the "hands-off" claim.
- Existing precedent: `openspec/changes/dor-dod-enforcement/` (#194,
  merged as #195) — same escape-hatch pattern (`SKIP_*` env vars),
  same "extend existing scripts" approach.
- Docs to cite but NOT modify:
  `docs/decisions/2026-08-21-revalidate-then-merge.md`,
  `docs/decisions/2026-08-21-pr-flow-hardening.md`,
  `docs/decisions/2026-08-23-fetch-before-launch-tooling.md`.
- External settings: no GH ruleset changes, no environment secret
  additions (the `HERMES_AUTOMATION_APP_*` variables/secrets from
  #213 are the ones the trust chain guide references but does not
  create).

## Tasks

Implementation checklist in dependency order:

- [ ] 1. Create `scripts/dev/pr-actor-check.sh` with self-test matrix
- [ ] 2. Wire `pr-actor-check.sh` into `scripts/dev/create-pr.sh` after openspec gate
- [ ] 3. Extend `scripts/dev/create-pr.sh` self-test with actor scenarios (exit code 4)
- [ ] 4. Modify `scripts/dev/changelog-check.sh` — add `require-fragment` state + update self-test
- [ ] 5. Add `check_pr_title_semantic` to `scripts/dev/pr-preflight.sh` + fixtures
- [ ] 6. Modify `scripts/dev/pr-preflight.sh` body-lint path (auto-render when `--body` absent)
- [ ] 7. Extend `scripts/dev/sub-agent-prompt-checklist.md` with items 5-9
- [ ] 8. Write `docs/guides/automation-trust-chain.md` (~150 LOC)
- [ ] 9. Append actor-check paragraph to `AGENTS.md` under rules 12-13
- [ ] 10. Create `changelog.d/wave-sdd-process-hardening.md`
- [ ] 11. Local `bash scripts/dev/pr-preflight.sh` green + all self-tests exit 0
- [ ] 12. Dogfood-open the PR: `bash scripts/dev/create-pr.sh --render-body sdd-process-hardening`
