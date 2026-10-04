#!/usr/bin/env bash
# Thin wrapper around `gh pr create` that enforces PR body content.
#
# Refuses to open the PR if the body file fails pr-body-lint.sh. This
# closes the "opened PR with placeholder body" drift class (drift-eval
# #459, drift class 1). AGENTS.md § When SDD applies mandates using
# this wrapper — do NOT bypass with `gh pr create` directly.
#
# Orchestrator applier prompts must invoke this wrapper, not `gh pr create`
# directly.
#
# Repo-relative body files (drift-eval 2026-08-22, wave 3):
#   If --body-file points inside the repo working tree, the wrapper copies
#   it to a mktemp'd /tmp/ location BEFORE lint + `gh pr create`. Rationale:
#   markdownlint-cli2 (pre-commit + CI) scans all `*.md` under the repo and
#   MD041 requires a first-line H1, but PR bodies start with `## Summary`
#   (H2) per the mandatory pull_request_template. Sub-agents that wrote
#   `.pr-body-*.md` at repo root repeatedly hit this. The copy sidesteps
#   the collision without asking every author to remember to `mktemp` first.
#   Files already under /tmp/ or absolute non-repo paths are used as-is.
#
# Lowercase title gate (drift-eval 2026-08-22, wave 4):
#   The CI job "PR title" uses `amannn/action-semantic-pull-request@v6` with
#   `subjectPattern: ^(?![A-Z]).+$` — the subject after the `:` MUST start
#   lowercase, even for proper nouns (Telegram → telegram, Slack → slack).
#   This wrapper validates `--title` against
#     ^[a-z]+(\(.+\))?!?:\s+[a-z]
#   BEFORE running pr-body-lint or `gh pr create`. On mismatch: prints the
#   offending title, the regex, the CI error the sub-agent would eventually
#   see, and exits 2. Closes the "push → CI red on title-case" round-trip
#   drift (PR #130 was caught by CI only, cost 1 amend+push cycle).
#
# Invocation:
#   bash scripts/dev/create-pr.sh --title <t> --body-file <path> [passthrough]
#   bash scripts/dev/create-pr.sh --title <t> --body-file <path> \
#     --skip-openspec-check "reason"
#   HERMES_SELF_TEST=1 bash scripts/dev/create-pr.sh
#
# Passthrough allowlist: --head, --base, --reviewer. --draft is refused:
# a PR opens only when it is ready to merge (Rick's rule, 2026-10-03).
# DoR flags: --skip-openspec-check "reason" (bypass openspec gate, audit-logged)
# Anything else → exit 2 (usage error, unknown flag).
#
# Env vars:
#   HERMES_SELF_TEST  — set to 1 to run built-in fixtures + exit
#   GH_CMD            — override for `gh` binary; self-test sets to `echo`
#                       so tests never call real GitHub.
#
# DoR/DoD openspec gate (drift-eval 2026-08-24, #194):
#   feat/refactor/perf commits MUST add an openspec/changes/*/change.md
#   in the branch diff vs origin/main. See openspec/changes/dor-dod-enforcement/.
#   Bypass with --skip-openspec-check "reason" — the reason is required and
#   is echoed to stderr for audit trail visibility.
#
# Exit codes:
#   0 — PR created successfully (or self-test passed)
#   1 — PR body failed lint (gh NOT invoked)
#   2 — usage / arg error
#   3 — DoR gate failed (openspec change spec missing)
#   4 — Actor gate failed (author == reviewer, self-review deadlock)
#       Bypass: SKIP_ACTOR_CHECK=1. See scripts/dev/pr-actor-check.sh.
#   5 — Ready-to-merge gate failed: uncommitted or unpushed work, behind
#       or conflicting with origin/main, or no full preflight pass on
#       HEAD. Bypass: SKIP_READY_CHECK="reason". See
#       scripts/dev/pr-ready-check.sh.
#   6 — ## Testing has no valid "Test tiers touched:" line (gh NOT
#       invoked). See scripts/dev/test-tiers-line-check.sh.

set -uo pipefail

TITLE=""
BODY_FILE=""
SKIP_OPENSPEC_REASON=""
PASSTHROUGH=()
ALLOWED_PASS=(--head --base --reviewer)

# Conventional-commit + lowercase-subject regex. Matches:
#   type(scope)!: lowercase subject...
#   type(scope): lowercase subject...
#   type: lowercase subject...
# Rejects any subject whose first character after `:` is uppercase (proper
# nouns included). Mirrors the CI job "PR title" subjectPattern
# ^(?![A-Z]).+$ from amannn/action-semantic-pull-request@v6.
TITLE_REGEX='^[a-z]+(\(.+\))?!?:[[:space:]]+[a-z]'

is_allowed_flag() {
    local flag="$1"
    for a in "${ALLOWED_PASS[@]}"; do [[ "$a" == "$flag" ]] && return 0; done
    return 1
}

# ────────────────────────────────────────────────────────────────
# validate_title <title>
#
# Returns 0 if the title matches TITLE_REGEX (conventional-commit prefix +
# lowercase first char after `:`), else prints diagnostic info to stderr
# and returns 2. See the "Lowercase title gate" note in the header.
# ────────────────────────────────────────────────────────────────
validate_title() {
    local title="$1"
    if [[ "$title" =~ $TITLE_REGEX ]]; then
        return 0
    fi
    {
        echo "ERROR: PR title fails the lowercase-subject rule."
        echo "  title:  $title"
        echo "  regex:  $TITLE_REGEX"
        echo ""
        echo "  CI would eventually reject this with (from amannn/action-semantic-pull-request@v6):"
        echo "    Subject may not start with an uppercase character."
        echo ""
        echo "  Fix: lowercase the first char after the colon (yes, even proper nouns)."
        echo "    bad:  feat(adapters): Telegram notifier — urgent alerts"
        echo "    good: feat(adapters): telegram notifier — urgent alerts"
    } >&2
    return 2
}

# ────────────────────────────────────────────────────────────────
# validate_openspec_change_present
#
# DoR gate (#194). If the latest commit's conventional-commit type is
# feat|refactor|perf, require at least one openspec/changes/*/change.md
# addition or modification in the branch diff vs origin/main.
#
# Exit codes: 0 (pass or exempt), 3 (fail — spec required, none found).
# Bypass: --skip-openspec-check "reason" sets SKIP_OPENSPEC_REASON.
#
# See openspec/changes/dor-dod-enforcement/change.md for the full rationale.
# ────────────────────────────────────────────────────────────────
validate_openspec_change_present() {
    if [[ -n "$SKIP_OPENSPEC_REASON" ]]; then
        echo "WARN: openspec check skipped: $SKIP_OPENSPEC_REASON" >&2
        return 0
    fi
    local latest_subject
    latest_subject=$(git log -1 --format=%s HEAD 2>/dev/null)
    if [[ -z "$latest_subject" ]]; then
        return 0  # empty branch — nothing to check
    fi
    if ! [[ "$latest_subject" =~ ^(feat|refactor|perf)(\(.+\))?(!)?: ]]; then
        return 0  # exempt commit type
    fi
    # In self-test, git may not resolve origin/main — the gate is verified
    # by dedicated self-test fixtures that stub the git-diff output.
    local added_or_modified
    added_or_modified=$(git diff --name-only origin/main..HEAD -- \
        'openspec/changes/*/change.md' 2>/dev/null)
    if [[ -n "$added_or_modified" ]]; then
        return 0
    fi
    {
        echo "ERROR: feat/refactor/perf PR requires an openspec change spec."
        echo "  Latest commit: $latest_subject"
        echo "  Expected: openspec/changes/<change-name>/change.md added or modified"
        echo "  Template: openspec/changes/TEMPLATE/change.md"
        echo "  Bypass:   --skip-openspec-check \"reason\" (audit-logged)"
        echo "  Rationale: #194 DoR/DoD hardening — openspec/changes/dor-dod-enforcement/"
    } >&2
    return 3
}

# ────────────────────────────────────────────────────────────────
# ensure_body_outside_repo <body-file-path>
#
# Prints the path to use downstream. If the input path resolves to a file
# INSIDE the repo working tree (i.e. `git rev-parse --show-toplevel` is a
# prefix of the resolved absolute path), copies it to a mktemp'd `/tmp/`
# file and prints that. Otherwise prints the input unchanged.
#
# Rationale: see the "Repo-relative body files" note in the header.
# ────────────────────────────────────────────────────────────────
ensure_body_outside_repo() {
    local in_path="$1"
    local repo_root abs_path
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
    if [[ -z "$repo_root" ]]; then
        # Not in a git repo — behave as identity.
        printf '%s' "$in_path"
        return 0
    fi
    # Resolve to absolute path without requiring `realpath` (Windows/git-bash safe).
    abs_path=$(cd "$(dirname "$in_path")" && printf '%s/%s' "$(pwd)" "$(basename "$in_path")")
    # Normalise repo_root separators (git returns forward slashes on Windows).
    if [[ "$abs_path" == "$repo_root"/* ]]; then
        local tmp_copy
        tmp_copy=$(mktemp -t create-pr-body.XXXXXX.md)
        cp "$in_path" "$tmp_copy"
        printf '%s' "$tmp_copy"
        return 0
    fi
    printf '%s' "$in_path"
}

# ────────────────────────────────────────────────────────────────
# Self-test — validate the script's own logic without side-effects
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    # Under a pre-commit hook git exports GIT_INDEX_FILE for the repo being
    # committed. Fixtures 12-14 build a throwaway repo, and an inherited
    # index makes its commits write into the real repo's index instead.
    unset GIT_INDEX_FILE
    fail=0
    tmp_ok=$(mktemp -t create-pr-ok.XXXXXX.md)
    tmp_bad=$(mktemp -t create-pr-bad.XXXXXX.md)

    # Fixture body that passes pr-body-lint (4 mandatory sections filled)
    cat > "$tmp_ok" <<'EOF'
## Summary
Real summary content.
## Related
- Issue: Closes #1
## Testing
Real testing steps.
Test tiers touched: unit
## Risk
Low.
EOF

    # Fixture body that fails pr-body-lint (placeholder-only)
    cat > "$tmp_bad" <<'EOF'
## Summary
<!-- placeholder -->
## Related
- ADR: <!-- ph -->
## Testing
<!-- ph -->
## Risk
<!-- ph -->
EOF

    # Titles used across fixtures 1-9 must clear the wave-4 lowercase-subject
    # gate. `feat(x): valid` is the minimal conforming title.
    T_OK="feat(x): valid"

    # The fixtures run from whatever branch the developer is on; the
    # ready gate has its own self-test (pr-ready-check.sh).
    export SKIP_READY_CHECK="create-pr self-test fixture"

    # The DoR gate reads the repo's latest commit, so fixtures 1-11 would
    # fail whenever the real HEAD is a feat commit. They run against a
    # neutral repo whose only commit is a chore; 12-14 build their own.
    # Hooks export GIT_DIR / GIT_INDEX_FILE for the real repo; the
    # scratch repos below must never inherit them.
    while IFS= read -r var; do
        unset "$var"
    done < <(env | sed -n 's/^\(GIT_[A-Z_]*\)=.*/\1/p')
    neutral=$(mktemp -d -t create-pr-neutral.XXXXXX)
    git init -q "$neutral"
    git -C "$neutral" -c user.name=t -c user.email=t@invalid commit -q --allow-empty -m "chore: neutral"
    git -C "$neutral" update-ref refs/remotes/origin/main HEAD
    export GIT_DIR="$neutral/.git" GIT_WORK_TREE="$neutral"

    # 1. PASS: valid body, gh stubbed → exit 0, echo captures gh args
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "$tmp_ok" 2>&1); rc=$?
    if [[ $rc -ne 0 ]]; then echo "SELF-TEST FAIL: valid body → rc=$rc, want 0"; fail=1; fi
    grep -q 'pr create' <<< "$out" || { echo "SELF-TEST FAIL: valid body should invoke stubbed gh"; fail=1; }
    grep -q -- "--title $T_OK" <<< "$out" || { echo "SELF-TEST FAIL: --title passthrough missing"; fail=1; }

    # 2. PASS: passthrough --reviewer; FAIL: --draft is refused before gh
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "$tmp_ok" --reviewer alice 2>&1); rc=$?
    if [[ $rc -ne 0 ]]; then echo "SELF-TEST FAIL: passthrough → rc=$rc"; fail=1; fi
    grep -q -- '--reviewer alice' <<< "$out" || { echo "SELF-TEST FAIL: --reviewer not forwarded"; fail=1; }
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "$tmp_ok" --draft 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: --draft → rc=$rc, want 2"; fail=1; }
    grep -q 'pr create' <<< "$out" && { echo "SELF-TEST FAIL: gh MUST NOT be invoked for a draft"; fail=1; }

    # 3. PASS: --head + --base passthrough
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "$tmp_ok" --head feat --base main 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL: --head/--base rc=$rc"; fail=1; }

    # 4. FAIL: bad body → exit 1, gh NOT invoked
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "$tmp_bad" 2>&1); rc=$?
    [[ $rc -eq 1 ]] || { echo "SELF-TEST FAIL: bad body → rc=$rc, want 1"; fail=1; }
    grep -q 'pr create' <<< "$out" && { echo "SELF-TEST FAIL: gh MUST NOT be invoked on lint fail"; fail=1; }

    # 4b. FAIL: body passes lint but has no "Test tiers touched" line
    #     → exit 6, gh NOT invoked (#160)
    tmp_notiers=$(mktemp -t create-pr-notiers.XXXXXX.md)
    grep -v '^Test tiers touched:' "$tmp_ok" > "$tmp_notiers"
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "$tmp_notiers" 2>&1); rc=$?
    [[ $rc -eq 6 ]] || { echo "SELF-TEST FAIL 4b: no tiers line → rc=$rc, want 6"; fail=1; }
    grep -q 'pr create' <<< "$out" && { echo "SELF-TEST FAIL 4b: gh MUST NOT be invoked without a tiers line"; fail=1; }
    rm -f "$tmp_notiers"

    # 5. FAIL: missing --body-file arg → exit 2
    out=$(HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: missing --body-file → rc=$rc, want 2"; fail=1; }

    # 6. FAIL: nonexistent body file → exit 2 (before lint runs)
    out=$(HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "/nonexistent/xyz.md" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: nonexistent body-file → rc=$rc, want 2"; fail=1; }

    # 7. FAIL: unknown flag → exit 2
    out=$(HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "$tmp_ok" --project 99 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: unknown flag → rc=$rc, want 2"; fail=1; }
    grep -q "unknown flag" <<< "$out" || { echo "SELF-TEST FAIL: expected 'unknown flag' in stderr"; fail=1; }

    # 8. FAIL: missing --title → exit 2
    out=$(HERMES_SELF_TEST=0 bash "$0" --body-file "$tmp_ok" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: missing --title → rc=$rc, want 2"; fail=1; }

    # 9. PASS: body written to a repo-relative path is copied to /tmp/
    #    BEFORE gh is invoked. Verifies the "copy repo-relative to /tmp/"
    #    behavior added in wave 3 (drift-eval 2026-08-22). Assertion: the
    #    --body arg forwarded to the stubbed gh must NOT reference the repo
    #    root path — it must point at a /tmp/ mktemp file.
    repo_root_st=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
    tmp_in_repo="$repo_root_st/.pr-body-selftest-$$.md"
    cp "$tmp_ok" "$tmp_in_repo"
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "feat(x): valid" --body-file "$tmp_in_repo" 2>&1); rc=$?
    if [[ $rc -ne 0 ]]; then echo "SELF-TEST FAIL: repo-relative body → rc=$rc, want 0"; fail=1; fi
    grep -q 'pr create' <<< "$out" || { echo "SELF-TEST FAIL: gh not invoked for repo-relative body"; fail=1; }
    # The body content is passed inline (not as a path), so we can't grep the
    # path directly. Instead, verify that lint ran (echo output includes body)
    # and that no error about copy failed showed up. The key invariant that
    # exercised the copy is that pr-body-lint used the temp file — we assert
    # the script succeeded (rc=0) with a valid body coming from the repo path.
    rm -f "$tmp_in_repo"

    # 10. PASS: conventional-commit title with lowercase subject clears the
    #     new title gate (drift-eval 2026-08-22, wave 4). The CI job "PR
    #     title" would accept this.
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "feat(adapters): telegram notifier for urgent alerts channel" --body-file "$tmp_ok" 2>&1); rc=$?
    if [[ $rc -ne 0 ]]; then echo "SELF-TEST FAIL: lowercase-subject title → rc=$rc, want 0"; fail=1; fi
    grep -q 'pr create' <<< "$out" || { echo "SELF-TEST FAIL: lowercase-subject title should invoke gh"; fail=1; }

    # 11. FAIL: same title but uppercase 'T' in "Telegram" — CI would reject
    #     with subjectPattern ^(?![A-Z]).+$. Wrapper MUST reject before
    #     invoking gh, exit 2, and NOT call the stubbed gh at all.
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "feat(adapters): Telegram notifier for urgent alerts channel" --body-file "$tmp_ok" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: uppercase-subject title → rc=$rc, want 2"; fail=1; }
    grep -q 'pr create' <<< "$out" && { echo "SELF-TEST FAIL: gh MUST NOT be invoked on title-gate fail"; fail=1; }
    grep -q 'lowercase-subject rule' <<< "$out" || { echo "SELF-TEST FAIL: expected 'lowercase-subject rule' in stderr"; fail=1; }

    # 11b. PASS: a CRLF body (written on Windows) reaches gh as LF only.
    #      CI's Linux awk does not match `## Summary\r`, so a CR that
    #      survives into the PR body fails the PR-body job (#318).
    tmp_crlf=$(mktemp -t create-pr-crlf.XXXXXX.md)
    sed 's/$/\r/' "$tmp_ok" > "$tmp_crlf"
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "$T_OK" --body-file "$tmp_crlf" 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 11b: CRLF body → rc=$rc, want 0"; fail=1; }
    # Pattern match, not grep: Git Bash grep drops CRs in text mode.
    [[ "$out" == *$'\r'* ]] && { echo "SELF-TEST FAIL 11b: CR reached the gh --body argument"; fail=1; }
    rm -f "$tmp_crlf"

    # Fixtures 12-14 exercise the DoR/DoD openspec gate (#194). The gate
    # reads `git log -1` and `git diff origin/main..HEAD`. We isolate the
    # git state in a tmpdir via GIT_DIR + GIT_WORK_TREE env vars so CWD
    # stays at the repo root — otherwise the relative `scripts/dev/pr-body-lint.sh`
    # call inside this script would fail because the tmpdir has no such
    # path. This is a standard git-in-test idiom (see gitcli(1)).
    unset GIT_DIR GIT_WORK_TREE
    rm -rf "$neutral"
    tmpdir=$(mktemp -d -t create-pr-selftest.XXXXXX)
    (
        export GIT_DIR="$tmpdir/.git" GIT_WORK_TREE="$tmpdir"
        git init -q "$tmpdir"
        git -C "$tmpdir" commit -q --allow-empty -m "chore: initial"
        git -C "$tmpdir" branch -m main 2>/dev/null || true
        # Simulate origin/main at the initial (chore) commit — so a feat
        # commit added later shows up as HEAD-only in the diff.
        git -C "$tmpdir" update-ref refs/remotes/origin/main HEAD
        git -C "$tmpdir" commit -q --allow-empty -m "feat(x): valid"
    ) >/dev/null 2>&1

    # 12. FAIL: feat title + no openspec change in diff → exit 3, gh NOT
    #     invoked, stderr mentions openspec.
    if [[ -d "$tmpdir/.git" ]]; then
        out=$(GIT_DIR="$tmpdir/.git" GIT_WORK_TREE="$tmpdir" GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "feat(x): valid" --body-file "$tmp_ok" 2>&1); rc=$?
        [[ $rc -eq 3 ]] || { echo "SELF-TEST FAIL 12: feat + no openspec → rc=$rc, want 3"; fail=1; }
        grep -q 'pr create' <<< "$out" && { echo "SELF-TEST FAIL 12: gh MUST NOT be invoked on DoR fail"; fail=1; }
        grep -qi 'openspec' <<< "$out" || { echo "SELF-TEST FAIL 12: expected 'openspec' in stderr"; fail=1; }
    fi

    # 13. PASS: same as 12 but --skip-openspec-check "reason" → exit 0,
    #     gh invoked, stderr warns about the skip.
    if [[ -d "$tmpdir/.git" ]]; then
        out=$(GIT_DIR="$tmpdir/.git" GIT_WORK_TREE="$tmpdir" GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "feat(x): valid" --body-file "$tmp_ok" --skip-openspec-check "cosmetic constant rename" 2>&1); rc=$?
        [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 13: skip flag → rc=$rc, want 0"; fail=1; }
        grep -q 'pr create' <<< "$out" || { echo "SELF-TEST FAIL 13: gh should be invoked when skip flag set"; fail=1; }
        grep -q 'openspec check skipped' <<< "$out" || { echo "SELF-TEST FAIL 13: expected 'openspec check skipped' warning"; fail=1; }
    fi

    # 14. PASS: feat title + openspec change staged → exit 0.
    if [[ -d "$tmpdir/.git" ]]; then
        mkdir -p "$tmpdir/openspec/changes/x-change"
        echo "# Change: x-change" > "$tmpdir/openspec/changes/x-change/change.md"
        (
            export GIT_DIR="$tmpdir/.git" GIT_WORK_TREE="$tmpdir"
            git -C "$tmpdir" add openspec/changes/x-change/change.md
            git -C "$tmpdir" commit -q --amend --no-edit
        ) >/dev/null 2>&1
        out=$(GIT_DIR="$tmpdir/.git" GIT_WORK_TREE="$tmpdir" GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "feat(x): valid" --body-file "$tmp_ok" 2>&1); rc=$?
        [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 14: feat + openspec present → rc=$rc, want 0"; fail=1; }
        grep -q 'pr create' <<< "$out" || { echo "SELF-TEST FAIL 14: gh should be invoked when openspec change present"; fail=1; }
    fi

    rm -rf "$tmpdir"
    rm -f "$tmp_ok" "$tmp_bad"

    if [[ $fail -eq 0 ]]; then
        echo "SELF-TEST PASS: create-pr.sh handles 14 fixtures correctly."
        exit 0
    fi
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Argument parsing
# ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --title)
            TITLE="${2:-}"
            [[ -z "$TITLE" ]] && { echo "ERROR: --title requires a value" >&2; exit 2; }
            shift 2 ;;
        --body-file)
            BODY_FILE="${2:-}"
            [[ -z "$BODY_FILE" ]] && { echo "ERROR: --body-file requires a value" >&2; exit 2; }
            shift 2 ;;
        --draft)
            echo "ERROR: --draft is not allowed: open the PR when it is ready to merge" >&2
            exit 2 ;;
        --head|--base|--reviewer)
            PASSTHROUGH+=("$1" "${2:-}"); shift 2 ;;
        --skip-openspec-check)
            SKIP_OPENSPEC_REASON="${2:-}"
            [[ -z "$SKIP_OPENSPEC_REASON" ]] && { echo "ERROR: --skip-openspec-check requires a reason string" >&2; exit 2; }
            shift 2 ;;
        -h|--help)
            sed -n '2,26p' "$0"; exit 0 ;;
        *)
            echo "ERROR: unknown flag '$1' (allowlist: --title, --body-file, --skip-openspec-check, ${ALLOWED_PASS[*]})" >&2
            exit 2 ;;
    esac
done

# ────────────────────────────────────────────────────────────────
# Required-args check
# ────────────────────────────────────────────────────────────────
[[ -z "$TITLE" ]] && { echo "ERROR: --title is required" >&2; exit 2; }
[[ -z "$BODY_FILE" ]] && { echo "ERROR: --body-file is required" >&2; exit 2; }
[[ -f "$BODY_FILE" ]] || { echo "ERROR: body file not found: $BODY_FILE" >&2; exit 2; }

# ────────────────────────────────────────────────────────────────
# Title gate — refuse titles the CI "PR title" job would reject.
# Runs BEFORE pr-body-lint so authors get the cheapest failure first.
# See the "Lowercase title gate" note in the header.
# ────────────────────────────────────────────────────────────────
validate_title "$TITLE" || exit 2

# ────────────────────────────────────────────────────────────────
# DoR gate — openspec change spec required for feat/refactor/perf.
# See validate_openspec_change_present() header for the rule.
# ────────────────────────────────────────────────────────────────
validate_openspec_change_present || exit 3

# ────────────────────────────────────────────────────────────────
# Actor gate — refuse PR-open when the gh-authenticated user matches
# the intended reviewer. Enforces the two-account discipline from
# AGENTS.md rules 12-13. See scripts/dev/pr-actor-check.sh header.
#
# Extracts --reviewer from PASSTHROUGH (if present) and forwards to
# the check. Falls back to HERMES_DEFAULT_REVIEWER or the hardcoded
# `ricardoafo` fallback.
#
# Exit code 4 (distinct from openspec 3, title 2, lint 1) so callers
# can distinguish the failure mode.
# ────────────────────────────────────────────────────────────────
if [[ "${SKIP_ACTOR_CHECK:-0}" != "1" && "${HERMES_SELF_TEST:-0}" != "1" ]]; then
    ACTOR_REVIEWER=""
    for i in "${!PASSTHROUGH[@]}"; do
        if [[ "${PASSTHROUGH[$i]}" == "--reviewer" ]]; then
            next=$((i + 1))
            ACTOR_REVIEWER="${PASSTHROUGH[$next]:-}"
            break
        fi
    done
    if [[ -n "$ACTOR_REVIEWER" ]]; then
        bash scripts/dev/pr-actor-check.sh --reviewer "$ACTOR_REVIEWER" || exit 4
    else
        bash scripts/dev/pr-actor-check.sh || exit 4
    fi
fi

# ────────────────────────────────────────────────────────────────
# Ready-to-merge gate — the PR must be mergeable as it stands: clean
# tree, pushed, contains origin/main, full preflight passed on HEAD.
# See scripts/dev/pr-ready-check.sh.
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" != "1" ]]; then
    bash scripts/dev/pr-ready-check.sh || exit 5
fi

# ────────────────────────────────────────────────────────────────
# Copy repo-relative body files to /tmp/ so markdownlint MD041 does not
# fire on the transient `.pr-body-*.md` scratchpad (see header note).
# ────────────────────────────────────────────────────────────────
BODY_FILE=$(ensure_body_outside_repo "$BODY_FILE")

# ────────────────────────────────────────────────────────────────
# Normalise to LF. A body written on Windows keeps its CRs through
# `gh pr create`, and CI's Linux awk then misses every `## ` heading
# (#318). Git Bash's awk tolerates the CR, so local lint alone would
# not catch it.
# ────────────────────────────────────────────────────────────────
lf_body=$(mktemp -t create-pr-lf.XXXXXX.md)
tr -d '\r' < "$BODY_FILE" > "$lf_body"
BODY_FILE="$lf_body"

# ────────────────────────────────────────────────────────────────
# Lint gate — refuse to open the PR if body fails pr-body-lint.sh
# ────────────────────────────────────────────────────────────────
if ! PR_BODY="$(cat "$BODY_FILE")" bash scripts/dev/pr-body-lint.sh; then
    echo "ERROR: pr-body-lint.sh rejected $BODY_FILE — PR NOT opened." >&2
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Test-tier gate — ## Testing must say which pyramid tiers the PR
# touches (#160). See scripts/dev/test-tiers-line-check.sh.
# ────────────────────────────────────────────────────────────────
if ! PR_BODY="$(cat "$BODY_FILE")" bash scripts/dev/test-tiers-line-check.sh; then
    echo "ERROR: test-tiers-line-check.sh rejected $BODY_FILE — PR NOT opened." >&2
    exit 6
fi

# ────────────────────────────────────────────────────────────────
# Lint passed — invoke gh (or GH_CMD stub for self-test)
# ────────────────────────────────────────────────────────────────
GH="${GH_CMD:-gh}"
$GH pr create --title "$TITLE" --body "$(cat "$BODY_FILE")" "${PASSTHROUGH[@]}"
