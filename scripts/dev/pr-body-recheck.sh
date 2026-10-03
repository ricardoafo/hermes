#!/usr/bin/env bash
# Re-run the CI "PR body" job when its result no longer matches the
# PR's current body.
#
# The PR body lives outside git, so editing it fires no push and the
# `CI` workflow does not re-run (#350). This script lints the current
# body and, when that verdict disagrees with the latest "PR body" job
# of the CI run for the PR's head commit, re-runs that job. GitHub
# re-runs its dependents with it, so "CI success" (the one required
# check) follows. The rest of the CI suite is not re-run.
#
# Called by:
#   - .github/workflows/pr-body-recheck.yml (on body edit, and when a
#     CI run completes — bot edits made with GITHUB_TOKEN fire no
#     `edited` event, so the completion trigger catches them)
#   - Manually: PR_NUMBER=123 bash scripts/dev/pr-body-recheck.sh
#
# Env vars:
#   PR_NUMBER          — the pull request to recheck (required)
#   GITHUB_REPOSITORY  — owner/repo (defaults to the gh-resolved repo)
#   GH_TOKEN           — needs pull-requests:read + actions:write
#   HERMES_SELF_TEST   — set to 1 to run the decision table and exit
#
# Exit codes:
#   0 — in sync, re-run requested, or nothing to do yet
#   2 — usage error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Bounds the edit → re-run → completion → recheck cycle should the
# linter ever disagree with itself.
MAX_RUN_ATTEMPT=5

# ────────────────────────────────────────────────────────────────
# decide <run_status> <job_conclusion> <body_verdict> <run_attempt>
# echoes one of: wait | skip | in-sync | rerun
# ────────────────────────────────────────────────────────────────
decide() {
    local run_status="$1" job_conclusion="$2" verdict="$3" attempt="$4"

    if [[ "$run_status" != "completed" ]]; then
        echo wait
    elif [[ "$job_conclusion" != "success" && "$job_conclusion" != "failure" ]]; then
        echo skip
    elif [[ "$job_conclusion" == "$verdict" ]]; then
        echo in-sync
    elif (( attempt >= MAX_RUN_ATTEMPT )); then
        echo skip
    else
        echo rerun
    fi
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    failed=0
    check() {
        local expected="$1"; shift
        local got
        got=$(decide "$@")
        if [[ "$got" == "$expected" ]]; then
            echo "PASS: decide $* -> $got"
        else
            echo "FAIL: decide $* -> $got, expected $expected"
            failed=1
        fi
    }
    check wait    in_progress failure success 1
    check rerun   completed   failure success 1
    check rerun   completed   success failure 1
    check in-sync completed   success success 1
    check in-sync completed   failure failure 2
    check skip    completed   skipped success 1
    check skip    completed   cancelled failure 1
    check skip    completed   failure success 5
    exit "$failed"
fi

: "${PR_NUMBER:?PR_NUMBER is required}"
repo="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"

pr_json=$(gh api "repos/$repo/pulls/$PR_NUMBER")
head_sha=$(jq -r '.head.sha' <<< "$pr_json")
body=$(jq -r '.body // ""' <<< "$pr_json")

run=$(gh api "repos/$repo/actions/workflows/ci.yml/runs?head_sha=$head_sha&event=pull_request&per_page=1" \
    --jq '.workflow_runs[0] // empty | [.id, .status, .run_attempt] | @tsv')
if [[ -z "$run" ]]; then
    echo "No CI run for $head_sha yet; nothing to recheck."
    exit 0
fi
read -r run_id run_status run_attempt <<< "$run"

job=$(gh api "repos/$repo/actions/runs/$run_id/jobs?per_page=100" \
    --jq '.jobs[] | select(.name == "PR body") | [.id, (.conclusion // "none")] | @tsv')
if [[ -z "$job" ]]; then
    echo "CI run $run_id has no PR body job; nothing to recheck."
    exit 0
fi
read -r job_id job_conclusion <<< "$job"

if PR_BODY="$body" bash "$SCRIPT_DIR/pr-body-lint.sh"; then
    verdict=success
else
    verdict=failure
fi

action=$(decide "$run_status" "$job_conclusion" "$verdict" "$run_attempt")
echo "PR #$PR_NUMBER: CI run $run_id ($run_status, attempt $run_attempt), PR body job $job_conclusion, current body $verdict -> $action"

case "$action" in
    wait)  echo "CI is still running; the recheck fires again when it completes." ;;
    rerun) gh api -X POST "repos/$repo/actions/jobs/$job_id/rerun" > /dev/null
           echo "Re-ran PR body job $job_id (and CI success)." ;;
esac
