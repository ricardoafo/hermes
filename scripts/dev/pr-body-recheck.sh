#!/usr/bin/env bash
# Re-run the CI "CI success" job when its "PR body" step no longer
# matches the PR's current body.
#
# The PR body lives outside git, so editing it fires no push and the
# `CI` workflow does not re-run (#350). This script lints the current
# body and, when that verdict disagrees with the "PR body" step of the
# latest CI run for the PR's head commit, re-runs the "CI success" job
# that holds it. That job is the whole suite and the one required check
# (#397), so the re-run refreshes the required result. It only happens
# when the verdict flips, which is rare.
#
# Called by:
#   - .github/workflows/pr-body-recheck.yml (on body edit, and when a
#     CI run completes — bot edits made with GITHUB_TOKEN fire no
#     `edited` event, so the completion trigger catches them)
#   - Manually: GITHUB_REPOSITORY=owner/repo GH_TOKEN=... PR_NUMBER=123 \
#       HERMES_RECHECK_DRY_RUN=1 bash scripts/dev/pr-body-recheck.sh
#
# Env vars:
#   PR_NUMBER          — the pull request to recheck (required)
#   GITHUB_REPOSITORY  — owner/repo (required)
#   GH_TOKEN           — needs pull-requests:read + actions:write
#   HERMES_RECHECK_DRY_RUN — set to 1 to report the decision without re-running
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

# The self-hosted runner has no gh or jq; curl + python3 are what it ships.
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required (owner/repo)}"
PYTHON="${PYTHON:-python3}"
repo="$GITHUB_REPOSITORY"

api() {
    curl -fsSL -X "${2:-GET}" \
        -H "Authorization: Bearer $GH_TOKEN" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "https://api.github.com/$1"
}

# json_get <python-expression over d>: prints the value, or nothing when None.
json_get() {
    "$PYTHON" -c '
import json, sys
d = json.load(sys.stdin)
v = eval(sys.argv[1], {}, {"d": d})
if v is not None:
    sys.stdout.write(v if isinstance(v, str) else "\t".join(map(str, v)))
' "$1"
}

pr_json=$(api "repos/$repo/pulls/$PR_NUMBER")
head_sha=$(json_get 'd["head"]["sha"]' <<< "$pr_json")
body=$(json_get 'd.get("body") or ""' <<< "$pr_json")

run=$(api "repos/$repo/actions/workflows/ci.yml/runs?head_sha=$head_sha&event=pull_request&per_page=1" \
    | json_get '[(r["id"], r["status"], r["run_attempt"]) for r in d["workflow_runs"][:1]][0] if d["workflow_runs"] else None')
if [[ -z "$run" ]]; then
    echo "No CI run for $head_sha yet; nothing to recheck."
    exit 0
fi
read -r run_id run_status run_attempt <<< "$run"

job=$(api "repos/$repo/actions/runs/$run_id/jobs?per_page=100" \
    | json_get 'next(((j["id"], s["conclusion"] or "none") for j in d["jobs"] if j["name"] == "CI success" for s in j.get("steps") or [] if s["name"] == "PR body"), None)')
if [[ -z "$job" ]]; then
    echo "CI run $run_id has no PR body step yet; nothing to recheck."
    exit 0
fi
read -r job_id job_conclusion <<< "$job"

if PR_BODY="$body" bash "$SCRIPT_DIR/pr-body-lint.sh"; then
    verdict=success
else
    verdict=failure
fi

action=$(decide "$run_status" "$job_conclusion" "$verdict" "$run_attempt")
echo "PR #$PR_NUMBER: CI run $run_id ($run_status, attempt $run_attempt), PR body step $job_conclusion, current body $verdict -> $action"

case "$action" in
    wait)  echo "CI is still running; the recheck fires again when it completes." ;;
    rerun)
        if [[ "${HERMES_RECHECK_DRY_RUN:-0}" == "1" ]]; then
            echo "Dry run: would re-run CI success job $job_id."
        else
            api "repos/$repo/actions/jobs/$job_id/rerun" POST > /dev/null
            echo "Re-ran CI success job $job_id."
        fi ;;
esac
