#!/usr/bin/env bash
# List the prod environment's secrets by age and flag the ones due for
# rotation (docs/runbook/secret-rotation.md, issue #679).
#
# Reads names and update dates only, through the GitHub API. GitHub never
# returns secret values, and this script never asks for them.
#
# Usage:
#   bash scripts/dev/secret-ages.sh [--max-days N] [--env NAME] [--repo OWNER/NAME]
#   HERMES_SELF_TEST=1 bash scripts/dev/secret-ages.sh
#
# Needs `gh` signed in as an account that can read the repo's environment
# secrets (the Code Owner's).
#
# Exit codes:
#   0 — every secret is younger than --max-days (default 90)
#   1 — at least one secret is due; the list says which
#   2 — usage error, or the API call failed

set -uo pipefail

MAX_DAYS=90
ENV_NAME=prod
REPO=ricardoafo/hermes

# Reads the API's {"secrets": [{"name", "updated_at"}, ...]} on stdin and
# prints one "<age-days> <name> <updated_at> DUE|ok" line per secret, oldest
# first. Exits 1 when any is DUE.
report() {
    python3 -c '
import json, sys
from datetime import datetime, timezone
max_days, now_text = int(sys.argv[1]), sys.argv[2]
now = datetime.fromisoformat(now_text) if now_text else datetime.now(timezone.utc)
rows = []
for s in json.load(sys.stdin).get("secrets", []):
    updated = datetime.fromisoformat(s["updated_at"].replace("Z", "+00:00"))
    rows.append(((now - updated).days, s["name"], s["updated_at"]))
due = False
for age, name, updated in sorted(rows, reverse=True):
    flag = "DUE" if age > max_days else "ok"
    due = due or flag == "DUE"
    print(f"{age:>5}d  {flag:<3}  {name}  (updated {updated})")
sys.exit(1 if due else 0)
' "$MAX_DAYS" "${SECRET_AGES_NOW:-}"
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    fixture='{"secrets":[{"name":"FRESH","updated_at":"2026-10-01T00:00:00Z"},{"name":"STALE","updated_at":"2026-06-01T00:00:00Z"}]}'
    export SECRET_AGES_NOW=2026-10-06T00:00:00+00:00

    out=$(printf '%s' "$fixture" | report); rc=$?
    [[ $rc -eq 1 ]] || { echo "SELF-TEST FAIL: a 127-day-old secret should exit 1, got $rc"; fail=1; }
    grep -q "127d  DUE  STALE" <<< "$out" || { echo "SELF-TEST FAIL: STALE not flagged DUE: $out"; fail=1; }
    grep -q "5d  ok   FRESH" <<< "$out" || { echo "SELF-TEST FAIL: FRESH not ok: $out"; fail=1; }
    [[ "$(head -1 <<< "$out")" == *STALE* ]] || { echo "SELF-TEST FAIL: oldest secret not listed first"; fail=1; }

    MAX_DAYS=200
    printf '%s' "$fixture" | report >/dev/null; rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL: nothing is over 200 days, want 0, got $rc"; fail=1; }

    if env -u HERMES_SELF_TEST bash "$0" --max-days nope >/dev/null 2>&1; then
        echo "SELF-TEST FAIL: a non-numeric --max-days was accepted"; fail=1
    fi

    [[ $fail -eq 0 ]] || { echo "SELF-TEST FAILED"; exit 1; }
    echo "SELF-TEST PASS: secret-ages.sh handles 5 fixtures."
    exit 0
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-days) MAX_DAYS="${2:-}"; shift 2 ;;
        --env) ENV_NAME="${2:-}"; shift 2 ;;
        --repo) REPO="${2:-}"; shift 2 ;;
        -h | --help) sed -n '2,19p' "$0"; exit 0 ;;
        *) echo "usage: $0 [--max-days N] [--env NAME] [--repo OWNER/NAME]" >&2; exit 2 ;;
    esac
done
if ! [[ "$MAX_DAYS" =~ ^[0-9]+$ ]]; then
    echo "ERROR: --max-days must be a whole number of days" >&2
    exit 2
fi

if ! json=$(gh api "repos/$REPO/environments/$ENV_NAME/secrets?per_page=100"); then
    echo "ERROR: could not list the $ENV_NAME environment's secrets (is gh signed in as the Code Owner?)" >&2
    exit 2
fi
echo "Secrets in the $ENV_NAME environment of $REPO, oldest first (limit ${MAX_DAYS} days):"
printf '%s' "$json" | report
