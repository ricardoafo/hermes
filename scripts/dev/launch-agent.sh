#!/usr/bin/env bash
# Pre-launch base-freshness gate for worktree-isolated sub-agents.
#
# Wave 6 (2026-08-23) tooling companion to AGENTS.md rule 4 ("Fetch
# origin/main BEFORE spawning a worktree-isolated sub-agent"). Rule 4
# stays as guidance; this script turns the guidance into an ENFORCED
# gate the orchestrator calls before spawning any sub-agent that will
# open a PR against `main`.
#
# What it does:
#   1. Runs `git fetch origin main` (quiet). Refuses to continue if the
#      fetch fails (offline, bad remote, etc.).
#   2. Compares local `main` to `origin/main`. If local `main` is BEHIND
#      origin, exits non-zero with the exact fast-forward command.
#   3. Prints the current `origin/main` SHA to stdout — the caller (or
#      the sub-agent launch prompt) can pin its base against this SHA
#      so mid-flight drift is at worst diff-able post-hoc.
#
# Why a NEW script (not extending pr-preflight.sh):
#   pr-preflight.sh runs BEFORE a `git push` — it catches stale bases
#   AT push time, once the sub-agent has already done all the work on
#   a stale base. This script runs BEFORE the sub-agent starts, which
#   avoids the "sub-agent wrote 800 lines against a stale main, now
#   rebase and re-run every self-test" cost. Both gates exist because
#   they close different windows.
#
# Invocation:
#   bash scripts/dev/launch-agent.sh
#   bash scripts/dev/launch-agent.sh --quiet          # SHA-only on stdout
#   HERMES_SELF_TEST=1 bash scripts/dev/launch-agent.sh
#
# Env vars:
#   HERMES_SELF_TEST — set to 1 to run built-in fixtures + exit
#   GIT_CMD          — override for `git`; self-test uses a stub that
#                      simulates fresh / stale / offline states without
#                      touching a real remote.
#
# Exit codes:
#   0 — local `main` is up-to-date with `origin/main` (safe to launch)
#   1 — local `main` is behind `origin/main` (launch BLOCKED)
#   2 — usage error, offline (fetch failed), or missing prerequisites

set -uo pipefail

QUIET=0
REMOTE="origin"
BRANCH="main"

# ────────────────────────────────────────────────────────────────
# Argument parsing
# ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quiet)
            QUIET=1; shift ;;
        --remote)
            REMOTE="${2:-}"
            [[ -z "$REMOTE" ]] && { echo "ERROR: --remote requires a value" >&2; exit 2; }
            shift 2 ;;
        --branch)
            BRANCH="${2:-}"
            [[ -z "$BRANCH" ]] && { echo "ERROR: --branch requires a value" >&2; exit 2; }
            shift 2 ;;
        -h|--help)
            sed -n '2,45p' "$0"; exit 0 ;;
        *)
            echo "ERROR: unknown flag '$1' (allowlist: --quiet, --remote, --branch)" >&2
            exit 2 ;;
    esac
done

# ────────────────────────────────────────────────────────────────
# check_base_fresh <remote> <branch>
#
# Returns:
#   0 — local <branch> is at OR ahead of <remote>/<branch> (safe)
#   1 — local <branch> is behind (BLOCKED, launch would inherit stale base)
#   2 — fetch failed (offline, bad remote, etc.)
#
# Uses $GIT_CMD (defaults to `git`) so the self-test can inject a stub.
# ────────────────────────────────────────────────────────────────
check_base_fresh() {
    local remote="$1"
    local branch="$2"
    local git_bin="${GIT_CMD:-git}"

    # 1. Fetch. Refuse to guess freshness offline — the whole point of
    #    the gate is to reflect the ACTUAL remote HEAD.
    if ! "$git_bin" fetch "$remote" "$branch" --quiet 2>/dev/null; then
        echo "ERROR: 'git fetch $remote $branch' failed." >&2
        echo "  Cause: offline, remote misconfigured, or auth missing." >&2
        echo "  Fix:   verify remote reachability, then re-run this script." >&2
        return 2
    fi

    local local_sha remote_sha
    local_sha=$("$git_bin" rev-parse "$branch" 2>/dev/null || echo "")
    remote_sha=$("$git_bin" rev-parse "$remote/$branch" 2>/dev/null || echo "")

    if [[ -z "$local_sha" || -z "$remote_sha" ]]; then
        echo "ERROR: could not resolve $branch or $remote/$branch." >&2
        echo "  local:  ${local_sha:-<unresolved>}" >&2
        echo "  remote: ${remote_sha:-<unresolved>}" >&2
        return 2
    fi

    # 2. Fast path — local at remote HEAD.
    if [[ "$local_sha" == "$remote_sha" ]]; then
        return 0
    fi

    # 3. Local ahead of remote is fine (unmerged local commits, e.g. the
    #    orchestrator itself is on a feature branch — this script cares
    #    about main freshness, not local uncommitted work).
    if "$git_bin" merge-base --is-ancestor "$remote/$branch" "$local_sha" 2>/dev/null; then
        return 0
    fi

    # 4. Local behind remote — BLOCK. Print the exact fast-forward.
    {
        echo "ERROR: local $branch is BEHIND $remote/$branch — launch BLOCKED."
        echo ""
        echo "  local  $branch: $local_sha"
        echo "  remote $remote/$branch: $remote_sha"
        echo ""
        echo "  Sub-agents spawned onto a stale base pay ~5% token overhead in mid-flight"
        echo "  rebase and can hit silent merge conflicts against races (AGENTS.md rule 4)."
        echo ""
        echo "  Fix from the primary tree (NOT this worktree):"
        echo "    git checkout $branch && git pull --ff-only $remote $branch"
        echo ""
        echo "  Then re-run: bash scripts/dev/launch-agent.sh"
    } >&2
    return 1
}

# ────────────────────────────────────────────────────────────────
# Self-test — validate the script's own logic with a git stub.
#
# The stub is a shell function exported as $GIT_CMD that simulates
# three states: fresh (local == remote), stale (local behind remote),
# offline (fetch fails). No real network calls, no real refs touched.
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    tmp_stub=$(mktemp -t launch-agent-git.XXXXXX)
    trap 'rm -f "$tmp_stub"' EXIT

    # ── Stub template ────────────────────────────────────────────
    # LAG_STATE controls behaviour:
    #   fresh   — fetch OK, local == remote (both AAA)
    #   stale   — fetch OK, local BBB, remote AAA (local behind — remote is
    #             the newer tip; merge-base ancestry AAA ancestor of BBB is false)
    #   ahead   — fetch OK, local CCC contains remote AAA as ancestor
    #             (local ahead of remote — legitimate feature branch case)
    #   offline — fetch fails
    #   norefs  — fetch OK, but rev-parse can't resolve
    #
    # The stub returns hard-coded SHAs so the check under test is the
    # ancestry / equality logic, not real git plumbing.
    cat > "$tmp_stub" <<'STUB'
#!/usr/bin/env bash
# git stub for launch-agent self-test — see stub template comment.
set -uo pipefail
case "${LAG_STATE:-fresh}" in
    fresh)
        case "$*" in
            "fetch origin main --quiet") exit 0 ;;
            "rev-parse main")            echo "AAA0000000000000000000000000000000000000"; exit 0 ;;
            "rev-parse origin/main")     echo "AAA0000000000000000000000000000000000000"; exit 0 ;;
            "merge-base --is-ancestor origin/main AAA0000000000000000000000000000000000000") exit 0 ;;
        esac ;;
    stale)
        case "$*" in
            "fetch origin main --quiet") exit 0 ;;
            "rev-parse main")            echo "BBB0000000000000000000000000000000000000"; exit 0 ;;
            "rev-parse origin/main")     echo "AAA0000000000000000000000000000000000000"; exit 0 ;;
            # Ancestry: remote AAA is NOT an ancestor of local BBB (local is on
            # a divergent older tip). Return non-zero to trigger the "behind" path.
            "merge-base --is-ancestor origin/main BBB0000000000000000000000000000000000000") exit 1 ;;
        esac ;;
    ahead)
        case "$*" in
            "fetch origin main --quiet") exit 0 ;;
            "rev-parse main")            echo "CCC0000000000000000000000000000000000000"; exit 0 ;;
            "rev-parse origin/main")     echo "AAA0000000000000000000000000000000000000"; exit 0 ;;
            # Local CCC contains remote AAA as ancestor — legitimate ahead.
            "merge-base --is-ancestor origin/main CCC0000000000000000000000000000000000000") exit 0 ;;
        esac ;;
    offline)
        case "$*" in
            "fetch origin main --quiet") exit 1 ;;
        esac ;;
    norefs)
        case "$*" in
            "fetch origin main --quiet") exit 0 ;;
            "rev-parse main")            echo ""; exit 1 ;;
            "rev-parse origin/main")     echo ""; exit 1 ;;
        esac ;;
esac
# Any command we did not stub is a self-test bug — fail loudly.
echo "STUB-BUG: unstubbed git command in state '${LAG_STATE:-fresh}': $*" >&2
exit 99
STUB
    chmod +x "$tmp_stub"

    # 1. PASS: fresh state → rc=0, stdout contains the remote SHA line
    out=$(LAG_STATE=fresh GIT_CMD="$tmp_stub" HERMES_SELF_TEST=0 bash "$0" 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL: fresh → rc=$rc, want 0"; fail=1; }
    grep -q 'AAA0000000000000000000000000000000000000' <<< "$out" \
        || { echo "SELF-TEST FAIL: fresh should print remote SHA"; fail=1; }

    # 2. PASS: fresh + --quiet → rc=0, stdout is SHA-only (no prose)
    out=$(LAG_STATE=fresh GIT_CMD="$tmp_stub" HERMES_SELF_TEST=0 bash "$0" --quiet 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL: --quiet fresh → rc=$rc"; fail=1; }
    if [[ "$out" != "AAA0000000000000000000000000000000000000" ]]; then
        echo "SELF-TEST FAIL: --quiet should print SHA only, got: '$out'"; fail=1
    fi

    # 3. FAIL: stale state → rc=1, stderr explains fix
    out=$(LAG_STATE=stale GIT_CMD="$tmp_stub" HERMES_SELF_TEST=0 bash "$0" 2>&1); rc=$?
    [[ $rc -eq 1 ]] || { echo "SELF-TEST FAIL: stale → rc=$rc, want 1"; fail=1; }
    grep -q 'is BEHIND' <<< "$out" \
        || { echo "SELF-TEST FAIL: stale should mention 'is BEHIND'"; fail=1; }
    grep -q 'git checkout main && git pull --ff-only' <<< "$out" \
        || { echo "SELF-TEST FAIL: stale should print exact fix command"; fail=1; }

    # 4. PASS: ahead state → rc=0 (local ahead is legitimate)
    out=$(LAG_STATE=ahead GIT_CMD="$tmp_stub" HERMES_SELF_TEST=0 bash "$0" 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL: ahead → rc=$rc, want 0"; fail=1; }

    # 5. FAIL: offline state → rc=2 (usage/env error, not launch-blocked)
    out=$(LAG_STATE=offline GIT_CMD="$tmp_stub" HERMES_SELF_TEST=0 bash "$0" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: offline → rc=$rc, want 2"; fail=1; }
    grep -q "git fetch origin main" <<< "$out" \
        || { echo "SELF-TEST FAIL: offline should mention failed fetch"; fail=1; }

    # 6. FAIL: unresolved refs → rc=2
    out=$(LAG_STATE=norefs GIT_CMD="$tmp_stub" HERMES_SELF_TEST=0 bash "$0" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: norefs → rc=$rc, want 2"; fail=1; }

    # 7. FAIL: unknown flag → rc=2
    out=$(HERMES_SELF_TEST=0 bash "$0" --nope 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: unknown flag → rc=$rc, want 2"; fail=1; }
    grep -q 'unknown flag' <<< "$out" \
        || { echo "SELF-TEST FAIL: expected 'unknown flag' in stderr"; fail=1; }

    if [[ $fail -eq 0 ]]; then
        echo "SELF-TEST PASS: launch-agent.sh handles 7 fixtures correctly."
        exit 0
    fi
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Production path
# ────────────────────────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git not found on PATH." >&2
    exit 2
fi

check_base_fresh "$REMOTE" "$BRANCH"
rc=$?
if [[ $rc -ne 0 ]]; then
    exit $rc
fi

# Use $GIT_CMD (defaults to git) so the self-test's stubbed rev-parse is
# what we echo back — otherwise the real git would leak into fixture output.
GIT="${GIT_CMD:-git}"
remote_sha=$("$GIT" rev-parse "$REMOTE/$BRANCH")
if [[ "$QUIET" == "1" ]]; then
    printf '%s\n' "$remote_sha"
else
    echo "✓ local $BRANCH is up-to-date with $REMOTE/$BRANCH"
    echo "  base SHA (pin sub-agent against this): $remote_sha"
fi
exit 0
