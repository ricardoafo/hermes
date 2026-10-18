# Change: pr-preflight-title-check

**SDD change**: `pr-preflight-title-check`
**Date**: 2026-08-26
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#255)

Mirror of engram topic key `sdd/pr-preflight-title-check/change`.

## Summary

Restores the semantic-PR-title check that was Tier-2-deferred in `sdd-process-hardening` (#215). Adds `check_pr_title_semantic` to `pr-preflight.sh` — mirrors the exact `TITLE_REGEX` from `create-pr.sh:77` and runs it against `git log -1 --format=%s HEAD`. Prevents the class of drift where a rebase or amend leaves an uppercase or non-conventional subject on HEAD that `create-pr.sh` accepted at open-time but `amannn/action-semantic-pull-request@v6` rejects at CI time, forcing a close-and-reopen cycle.

## Scope

### In

- **MOD**: `scripts/dev/pr-preflight.sh` — new function `check_pr_title_semantic` inserted just after preconditions (before check 1). Reads latest commit subject, validates against the same regex `create-pr.sh` uses. Bypass: `SKIP_TITLE_CHECK=1` (documented for rebase-in-progress + fixup workflows where HEAD is temporarily a squash target).
- **NEW**: `changelog.d/wave-pr-preflight-title-check.md`.

### Out

- Extracting `TITLE_REGEX` into a shared shell library. Two files with a hard comment link is cheaper than a new source dependency, and the drift risk is low (both files live under `scripts/dev/`, both are edited together in every title-rule change).
- Retro-lint of already-open PR titles. Old PRs are grandfathered.
- Removing the CI-side `amannn/action-semantic-pull-request@v6` job. Belt-and-suspenders — CI stays as the authoritative gate.

## Decisions

### D-1: Duplicate `TITLE_REGEX`, don't `source create-pr.sh`

- **Decision**: copy the regex literal into `pr-preflight.sh` with a `# MUST MATCH create-pr.sh:NN` comment.
- **Alternatives**: `source scripts/dev/create-pr.sh` (executes whole script), extract regex into `scripts/dev/lib/title-regex.sh`.
- **Rationale**: `source create-pr.sh` runs its whole main flow — needs early-return guard, refactor risk. A shared library file adds one more artifact to a `scripts/dev/` directory already at 20+ files. Two-line duplication with an anchor comment is unambiguous and cheap to keep in sync.
- **Cost of reversal**: LOW — extract to a shared file if the regex evolves and drift becomes a real problem.

### D-2: `SKIP_TITLE_CHECK=1` bypass

- **Decision**: env-var escape hatch, no other options.
- **Alternatives**: no bypass; heuristic detection of rebase-in-progress via `.git/rebase-merge/`.
- **Rationale**: Consistent with every other pr-preflight check's bypass pattern (`SKIP_OPENSPEC_CHECK`, `SKIP_ACTOR_CHECK`, `SKIP_PROJECT_CHECK`, `SKIP_BODY_CHECK`). Heuristic detection would silently pass during rebases even when the operator forgot to squash. Explicit bypass with a logged reason is the pattern the codebase already trusts.
- **Cost of reversal**: LOW.

### D-3: Fires against HEAD only, not against every commit in the branch

- **Decision**: `git log -1 --format=%s HEAD`.
- **Alternatives**: `git log origin/main..HEAD --format=%s` and validate every commit.
- **Rationale**: PR title == HEAD subject in the create-pr.sh flow (via `--title-from-commit`), which is the only supported path per the process. Intermediate commits get squashed at merge — their titles don't survive. Checking every commit would false-positive on WIP commits per `sub-agent-prompt-checklist.md` item 1.
- **Cost of reversal**: LOW — swap the git command.

## Requirements

### R-1: Uppercase subject fails the check

- **A-1.1**: With a branch whose HEAD subject is `feat(adapters): Foo bar` (uppercase F), `bash scripts/dev/pr-preflight.sh` fails with check name `PR title semantic (conventional-commit + lowercase)` and exit 1.
- **A-1.2**: The failure message shows the offending title, the regex, and the fix hint (matches `create-pr.sh`'s `validate_title` output for consistency).

### R-2: Non-conventional prefix fails

- **A-2.1**: With a HEAD subject like `Fixed the thing`, the check fails with the same output shape as A-1.2.

### R-3: Valid subject passes

- **A-3.1**: With HEAD subject `feat(adapters): telegram notifier — urgent alerts`, the check passes.

### R-4: Bypass respected

- **A-4.1**: `SKIP_TITLE_CHECK=1 bash scripts/dev/pr-preflight.sh` skips with a WARN line.

### R-5: Regex parity with `create-pr.sh`

- **A-5.1**: `grep -F 'TITLE_REGEX=' scripts/dev/pr-preflight.sh` returns a line identical to `grep -F 'TITLE_REGEX=' scripts/dev/create-pr.sh` (both regex literals match byte-for-byte).

## Design

```bash
# Insert in pr-preflight.sh after preconditions:

# MUST MATCH create-pr.sh:77 — same TITLE_REGEX; single source of truth.
# If you change one, change both AND update this comment's line number.
TITLE_REGEX='^[a-z]+(\(.+\))?!?:[[:space:]]+[a-z]'

# Check: PR title semantic (conventional-commit + lowercase subject).
# Mirrors amannn/action-semantic-pull-request@v6 subjectPattern.
# Rebase / fixup workflows may leave HEAD as a temporary commit; bypass
# with SKIP_TITLE_CHECK=1 with a logged reason.
check_pr_title_semantic() {
    if [[ "${SKIP_TITLE_CHECK:-0}" == "1" ]]; then
        echo "  WARN: title check skipped via SKIP_TITLE_CHECK=1" >&2
        return 0
    fi
    local title
    title=$(git log -1 --format=%s HEAD 2>/dev/null)
    if [[ -z "$title" ]]; then
        return 0
    fi
    if [[ "$title" =~ $TITLE_REGEX ]]; then
        return 0
    fi
    {
        echo "ERROR: HEAD commit subject fails the semantic-PR-title rule."
        echo "  title:  $title"
        echo "  regex:  $TITLE_REGEX"
        echo ""
        echo "  CI would reject this via amannn/action-semantic-pull-request@v6:"
        echo "    'Subject may not start with an uppercase character.'"
        echo ""
        echo "  Fix: lowercase the first char after the colon (yes, even proper nouns)."
        echo "  Bypass (rebase in progress): SKIP_TITLE_CHECK=1 bash scripts/dev/pr-preflight.sh"
    } >&2
    return 1
}
run_check "PR title semantic (conventional-commit + lowercase)" check_pr_title_semantic
```

## Test plan

1. `bash scripts/dev/pr-preflight.sh` on this branch — passes (title is properly lowercased).
2. Create a scratch commit with `Foo: bar` subject, `bash scripts/dev/pr-preflight.sh` — fails with the expected message. Reset, no drift.
3. `SKIP_TITLE_CHECK=1 bash scripts/dev/pr-preflight.sh` after the same scratch commit — skips with WARN.
4. `grep -F 'TITLE_REGEX=' scripts/dev/pr-preflight.sh scripts/dev/create-pr.sh` — regexes match.

## Risks

- **R1** — The two regex literals drift. Mitigation: anchor comment (`MUST MATCH create-pr.sh:NN`) + R-5 acceptance criterion. If more callers grow, promote to shared lib.
- **R2** — Rebase workflows fire the check on a fixup commit and confuse the operator. Mitigation: bypass env var + explicit "rebase in progress" hint in the error message.

## Dependencies on other changes

- Predecessor: #215 (`sdd-process-hardening`) — this closes the Tier-2 defer noted there.
- Complementary: PR #254 (`pr-preflight-project-check`) is another pr-preflight extension shipping in the same wave; they don't interact.
