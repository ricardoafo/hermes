# Change: dor-dod-enforcement

**SDD change**: `dor-dod-enforcement`
**Date**: 2026-08-24
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#194)

Mirror of engram topic key `sdd/dor-dod-enforcement/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Enforce the openspec-change-first discipline that #194 codifies: every
`feat|refactor|perf` PR MUST add an `openspec/changes/<name>/change.md`
before the code, or be explicitly opted out via `--skip-openspec-check`
with a logged reason. Enforcement lives at TWO layers: (1)
`scripts/dev/create-pr.sh` refuses to open the PR when the gate fails;
(2) `scripts/dev/pr-preflight.sh` runs the same check as part of the
mandatory pre-push suite. Third layer — AGENTS.md rule 15 — tells the
sub-agent orchestrator to verify the change spec BEFORE delegating any
Write / Edit tool. CI job + pre-commit hook deferred to a follow-up PR
so this change stays reviewable. This change dogfoods itself:
`openspec/changes/dor-dod-enforcement/change.md` exists (this file).

## Scope

### In

- **`scripts/dev/pr-preflight.sh` gate** — new check
  `openspec-change-required`. Runs after the CHANGELOG check. If the
  latest commit's conventional-commit type is `feat|refactor|perf` AND
  the branch's diff vs `origin/main` does NOT include any
  `openspec/changes/*/change.md` addition or modification, fail with a
  message pointing at `openspec/changes/TEMPLATE/`.
- **`scripts/dev/create-pr.sh` gate** — new flag
  `--skip-openspec-check` (default: check enabled). When the check is
  enabled, invoke the same predicate as pr-preflight. Fail with exit
  code 3 (distinct from title-gate exit 2 and lint exit 1) so callers
  can distinguish the failure mode.
- **`AGENTS.md` rule 15** — new rule under the § When SDD applies
  section: "Sub-agent implementation tasks (write code) MUST be
  preceded by an openspec change spec if the task is sized `size:m`
  or larger. The orchestrator MUST verify
  `openspec/changes/<name>/change.md` exists before delegating any
  Write / Edit tool."
- **Self-tests**: extend `HERMES_SELF_TEST=1 create-pr.sh` fixtures
  with 3 new cases (feat title + missing spec → fail; feat title +
  present spec → pass; feat title + `--skip-openspec-check` → pass
  with warning). Extend `pr-preflight.sh` self-test with a matching
  matrix.
- **Changelog fragment** at `changelog.d/wave-dor-dod-enforcement.md`.

### Out

- **CI job `openspec-status`** in `.github/workflows/ci.yml` —
  follow-up PR. Cannot land in this PR because a new required check
  needs the branch-protection ruleset updated as a separate step.
- **Pre-commit hook `openspec-change-required.sh`** — follow-up PR.
  Pre-commit changes ripple to every branch; kept isolated to reduce
  blast radius.
- **`fix:` PRs** are exempt for now (bugfixes rarely need a spec;
  operator can label a `fix:` PR `size:m+` on a case-by-case basis
  and the check will be re-added via a size-label lookup in a follow-
  up).
- **Retroactive spec-writing** for the ~180 already-shipped PRs is
  explicitly out of scope. Forward-looking only.
- **Size-label reading from linked GH issue** — this PR uses the
  conventional-commit prefix as the primary heuristic to avoid a `gh`
  call inside pre-flight (would break offline and slow every push).
  A follow-up may add a `gh`-based size lookup as an optional layer.

## Decisions

### D-1: Enforce at both create-pr.sh AND pr-preflight.sh (not one of the two)

- **Decision**: Both wrappers run the same predicate. `create-pr.sh`
  catches sub-agents that open a PR from an uncommitted branch;
  `pr-preflight.sh` catches manual `git push` from operator's shell.
- **Alternatives**: (a) create-pr.sh only — misses direct `git push`;
  (b) pr-preflight.sh only — misses PRs opened via GH web UI or
  `gh pr create` bypass.
- **Rationale**: two independent gates on two different push paths
  match the belt-and-suspenders discipline already in use for the
  three-gate live-trading rule.
- **Cost of reversal**: LOW — remove the check from either script.

### D-2: Conventional-commit prefix, not GH issue size label

- **Decision**: The predicate reads the latest commit's subject line
  and matches `^(feat|refactor|perf)(\(.+\))?(!)?:`. No `gh` call.
- **Alternatives**: (a) read `size:X` label from linked GH issue via
  `gh issue view` — accurate but requires network + auth + adds
  latency to every pre-flight; (b) parse PR body — same problem;
  (c) parse branch name (`feat/...`) — brittle for branches that use
  different prefixes.
- **Rationale**: conventional-commit prefixes are already enforced by
  the CI `PR title` job and required in every commit message.
  Reading the latest commit is zero-cost and works offline. Coarser
  than a size label but false positives (small `feat`) can bypass
  with `--skip-openspec-check`, and false negatives (large `fix`) are
  caught in code review.
- **Cost of reversal**: LOW — swap the predicate to `gh` lookup.

### D-3: Escape hatch = `--skip-openspec-check` with logged reason

- **Decision**: `create-pr.sh --skip-openspec-check "reason"` bypasses
  the check. The reason is required (empty string → exit 2) and gets
  logged to stderr. `pr-preflight.sh` bypasses via the env var
  `SKIP_OPENSPEC_CHECK=1`.
- **Alternatives**: (a) no escape hatch — brittle for edge cases like
  emergency rollback PRs; (b) magic string in commit message — hidden
  and easy to forget in the PR discussion.
- **Rationale**: escape hatches should be visible + explicit + logged.
  An audit trail exists in the PR body (`create-pr.sh` echoes the
  reason into the body's Risk section automatically — future work) or
  in the stderr transcript of the local pre-flight.
- **Cost of reversal**: LOW — remove the flag.

## Requirements

Each requirement is a testable statement. Each acceptance criterion is
a shell-verifiable check.

### R-1: `pr-preflight.sh` fails on feat/refactor/perf branch with no openspec change diff

- **A-1.1**: `HERMES_SELF_TEST=1 bash scripts/dev/pr-preflight.sh`
  exit 0. The existing self-test still passes.
- **A-1.2**: On a branch with the latest commit subject
  `feat(x): add y` and no `openspec/changes/*/change.md` in the diff
  vs origin/main, the openspec check fails with a message pointing to
  `openspec/changes/TEMPLATE/`. Verified via the extended self-test.
- **A-1.3**: On the same branch with `SKIP_OPENSPEC_CHECK=1`, the
  check is skipped with a stderr warning.

### R-2: `create-pr.sh` refuses to open PR when the openspec gate fails

- **A-2.1**: `HERMES_SELF_TEST=1 bash scripts/dev/create-pr.sh` exit 0.
  The existing 11 fixtures still pass.
- **A-2.2**: New fixture 12 — `feat(x): valid` title + valid body +
  no openspec change staged → exit 3, gh NOT invoked, stderr contains
  `openspec change required`.
- **A-2.3**: New fixture 13 — same as 12 + `--skip-openspec-check
  "emergency rollback"` → exit 0, gh invoked, stderr contains
  `WARN: openspec check skipped: emergency rollback`.
- **A-2.4**: New fixture 14 — `feat(x): valid` title + valid body +
  openspec change file staged → exit 0, gh invoked.

### R-3: `AGENTS.md` rule 15 exists

- **A-3.1**: `grep -q '^### Rule 15' AGENTS.md` OR similar heading
  match returns 0. Content includes "openspec change spec if the task
  is sized `size:m` or larger" verbatim.

### R-4: Dogfood

- **A-4.1**: `openspec/changes/dor-dod-enforcement/change.md` (this
  file) exists.
- **A-4.2**: `openspec/changes/dor-dod-enforcement/issue.md` exists
  and its data block references `issue: 194`.

## Design

Copy-ready content for the apply phase.

### New function in `pr-preflight.sh` (added after check #12)

```bash
# 13. Openspec change spec present when the PR's conventional-commit type
#     implies a material change (feat|refactor|perf). Coarse heuristic:
#     the latest commit subject drives it. False positives bypass via
#     SKIP_OPENSPEC_CHECK=1 with a logged reason. See #194 for the DoR/DoD
#     rationale.
check_openspec_change_required() {
    if [[ "${SKIP_OPENSPEC_CHECK:-0}" == "1" ]]; then
        echo "  WARN: openspec check skipped via SKIP_OPENSPEC_CHECK=1" >&2
        return 0
    fi
    local latest_subject
    latest_subject=$(git log -1 --format=%s HEAD 2>/dev/null)
    if [[ -z "$latest_subject" ]]; then
        return 0  # empty branch — nothing to check
    fi
    if ! [[ "$latest_subject" =~ ^(feat|refactor|perf)(\(.+\))?(!)?: ]]; then
        return 0  # docs/chore/ci/build/test/fix/revert — exempt
    fi
    # Look for any openspec/changes/*/change.md added or modified in the
    # branch's diff vs origin/main.
    local added_or_modified
    added_or_modified=$(git diff --name-only origin/main..HEAD -- \
        'openspec/changes/*/change.md' 2>/dev/null)
    if [[ -z "$added_or_modified" ]]; then
        {
            echo "ERROR: feat/refactor/perf PR requires an openspec change spec."
            echo "  Latest commit: $latest_subject"
            echo "  Expected: openspec/changes/<change-name>/change.md added or modified in this branch."
            echo "  Template: openspec/changes/TEMPLATE/change.md"
            echo "  Bypass (with cause): SKIP_OPENSPEC_CHECK=1 bash scripts/dev/pr-preflight.sh"
            echo "  Rationale: #194 DoR/DoD hardening."
        } >&2
        return 1
    fi
    return 0
}
run_check "Openspec change spec present (DoR)" check_openspec_change_required
```

### New flag + gate in `create-pr.sh`

Add `--skip-openspec-check REASON` to the arg parser and a
`validate_openspec_change_present()` function that runs the same
predicate as pr-preflight. On fail: exit 3 with a message pointing at
the template. On skip: emit stderr warning with the reason.

Placement: after `validate_title` (early failure preferred), before
`ensure_body_outside_repo`.

### `AGENTS.md` insertion (under § When SDD applies)

```markdown
### Rule 15 — openspec change spec is mandatory for feat/refactor/perf work

Sub-agent implementation tasks (write code) MUST be preceded by an
openspec change spec if the task is sized `size:m` or larger. The
orchestrator MUST verify
`openspec/changes/<change-name>/change.md` exists BEFORE delegating any
`Write` or `Edit` tool.

Enforcement: `scripts/dev/create-pr.sh` and
`scripts/dev/pr-preflight.sh` both reject `feat|refactor|perf` PRs
that add no openspec change spec to the branch diff. Bypass with
`--skip-openspec-check "reason"` (create-pr.sh) or
`SKIP_OPENSPEC_CHECK=1` (pr-preflight.sh) — reason MUST be logged.

Rationale: #194. Ships alongside `dor-dod-enforcement` change.
```

## Test plan

1. Every A-x.y acceptance criterion above has a matching self-test
   fixture in `HERMES_SELF_TEST=1 create-pr.sh` or `pr-preflight.sh`.
2. `bash scripts/dev/pr-preflight.sh` on THIS branch passes (dogfood:
   `openspec/changes/dor-dod-enforcement/change.md` exists, so the new
   check passes even though title is `chore:` — but we can flip the
   commit type to `feat` at merge time to prove the enforcement
   actually fires against itself).
3. `HERMES_SELF_TEST=1 bash scripts/dev/create-pr.sh` — all 14
   fixtures pass.
4. Manual smoke: create a throwaway branch with `feat: dummy` commit
   and no openspec change, run `pr-preflight.sh` — must fail with the
   new error message.

## Risks

- **Risk 1 — Coarse conventional-commit heuristic false-positive**.
  A tiny `feat: rename constant` doesn't need a spec but will be
  blocked. Mitigation: `--skip-openspec-check "cosmetic constant
  rename"` escape hatch with logged reason. If false positives become
  frequent, the follow-up size-label lookup is the correct
  refinement.
- **Risk 2 — Branch-name/directory mismatch**. Contributor creates
  `openspec/changes/foo-bar/change.md` on a branch named
  `feat/baz-qux`. The check passes because it only counts additions,
  not name-matching. Weaker than an exact match, but requiring exact
  name match would fail for PRs that split into multiple changes.
  Mitigation: code review is the human layer.
- **Risk 3 — This PR itself bypasses the rule**. This PR's latest
  commit will be `chore(process): dor-dod-enforcement` — exempt by
  design. Dogfood proof is the `openspec/changes/dor-dod-enforcement/`
  directory in the diff, not the commit prefix.

## Dependencies on other changes

- **#194 (proposal)**: this PR implements the minimum-viable
  enforcement described in the issue. CI job + pre-commit hook are
  follow-ups.
- **AGENTS.md**: rule 15 addition. Renumbering not required (existing
  rules end at 14 as of 2026-08-24).
- **`docs/architecture/adr-009-documentation-strategy.md` §6**: the
  dual-source rule this enforcement makes concrete. Not modified.
- **Shadow-mode ADR + change spec (branch `docs/adr-shadow-mode-architecture`)**:
  will be re-verified against this enforcement once this PR merges.
  That branch already has an openspec change spec, so it will pass.
