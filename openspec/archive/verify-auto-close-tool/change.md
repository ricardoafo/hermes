# Change: verify-auto-close-tool

**SDD change**: `verify-auto-close-tool`
**Date**: 2026-08-26
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#264)

Mirror of engram topic key `sdd/verify-auto-close-tool/change`.

## Summary

Adds `scripts/dev/verify-auto-close.sh` — an audit tool that walks the last N merged PRs, extracts `Closes #NNN` from each body, queries the linked issues' current state, and reports auto-close success/failure per PR. Gives the operator a fast way to spot-check the merge-queue's v3 belt-and-suspenders (PR #262) after every merge, without hunting through workflow logs. Exit 0 iff every linked issue is CLOSED; exit 1 on any straggler. Suitable for post-merge spot-check, cron audit, or CI gate.

## Scope

### In

- **NEW**: `scripts/dev/verify-auto-close.sh` — arg-parseable audit tool:
  - Default: last 10 merged PRs.
  - `--limit N`: audit last N merged PRs.
  - `--since <ISO-date>`: only PRs merged after this date.
  - `--quiet`: exit-code only, no per-PR output.
  - `--json`: emit JSON report for machine consumption.
- Per PR: extract `Closes|Fixes|Resolves #(\d+)` from body (same regex as v3), query each linked issue's state via `gh issue view`, distinguish CLOSED from OPEN.
- Per PR: pull the corresponding merge-queue workflow run's log, grep for the v3 diagnostic lines (`closed linked issue #NN` OR `failed to close linked issue #NN`). Distinguishes three cases:
  1. `closed linked issue` in log + issue CLOSED → v3 worked.
  2. `failed to close linked issue` in log + issue OPEN → v3 fired but token/permission issue.
  3. Neither diagnostic in log → v3 didn't run (pre-v3 merge, or workflow was on a different code path).
- `HERMES_SELF_TEST=1` fixture matrix — asserts regex parsing + report shape, no live `gh` calls.
- **NEW**: `changelog.d/wave-verify-auto-close-tool.md`.

### Out

- Scheduled workflow that runs this hourly. Manual invocation is fine for now; CI wiring later if operators want alerts.
- Automatic remediation (closing the straggler issue). Report-only; the operator decides whether to manually close or dig deeper.
- Historical audit beyond `--since 2026-08-01` — the API rate limit constrains large sweeps.

## Decisions

### D-1: Report-only, no remediation

- **Decision**: the tool never closes an issue. Reports only.
- **Alternatives**: `--remediate` flag that closes any straggler.
- **Rationale**: Report + human decision is the safe default. Silent auto-remediation could mask a deeper systemic failure (e.g. bug in the regex, or v3 code regression). Once operators trust the tool, a remediation flag can be added.
- **Cost of reversal**: LOW.

### D-2: Two data sources — issue state + workflow log — union interpreted

- **Decision**: query both `gh issue view` (current state) AND `gh run view --log` (v3 diagnostic markers). Distinguish "v3 didn't run" from "v3 ran but failed" from "v3 succeeded".
- **Alternatives**: only query issue state. Simpler but doesn't distinguish v3 regression from a manual close after the fact.
- **Rationale**: The user's exact concern is "we really need to make sure issues are closing" — meaning verify the AUTOMATION works, not just that issues eventually get closed. Log correlation is the difference.
- **Cost of reversal**: LOW — drop the log query, tool still works (loses one diagnostic distinction).

### D-3: Default `--limit 10`, not `--all`

- **Decision**: default to last 10 merged PRs.
- **Alternatives**: default to all PRs merged since a fixed date.
- **Rationale**: `gh api` rate limit is 5000/hour. 10 PRs × 3 API calls each (PR view + N issue views + workflow log) is ~30 calls — safely inside. All-PRs sweep on repo with hundreds of PRs hits the ceiling. Operators can override with `--limit N` for a full audit.
- **Cost of reversal**: LOW.

### D-4: `--json` output is `jq`-friendly

- **Decision**: emit an array of `{pr, linkedIssues: [{number, state}], diagnostic}` objects.
- **Alternatives**: emit YAML, ndjson, human-only.
- **Rationale**: JSON is trivially consumable by `jq`, importable by future CI gates or dashboards. YAML would need a separate parser. Ndjson is nicer for streaming but overkill for < 100 PRs.
- **Cost of reversal**: LOW — add other formats behind flags.

## Requirements

### R-1: Correctly identifies auto-close successes and failures

- **A-1.1**: `bash scripts/dev/verify-auto-close.sh --limit 5` reports per-PR status; exit 0 iff every linked issue is CLOSED.
- **A-1.2**: For a PR with an open linked issue AND `failed to close linked issue` in its merge-queue log, report includes `v3 fired but issue OPEN — token/permission bug` marker.
- **A-1.3**: For a PR with `Closes #N` in body but no `closed linked issue` NOR `failed to close linked issue` in log, report includes `v3 did not run — pre-v3 or wrong code path` marker.

### R-2: JSON output structure is stable

- **A-2.1**: `bash scripts/dev/verify-auto-close.sh --json --limit 3 | jq 'length'` returns 3.
- **A-2.2**: Each element has keys `pr`, `linked_issues`, `diagnostic`.

### R-3: Self-test harness

- **A-3.1**: `HERMES_SELF_TEST=1 bash scripts/dev/verify-auto-close.sh` runs the regex + report-shape fixtures, exits 0 on pass.

### R-4: No side effects

- **A-4.1**: The tool NEVER closes an issue, edits a PR, or writes to the workflow log. Verified by inspection (no `gh issue close`, no `gh api ... -X POST`).

## Design

Script skeleton (~230 LOC target):

```bash
#!/usr/bin/env bash
# Audit tool — spot-check whether merged PRs' linked issues actually closed.
# See openspec/archive/verify-auto-close-tool/change.md.

set -uo pipefail

LIMIT=10
SINCE=""
QUIET=0
JSON=0

while [ $# -gt 0 ]; do
    case "$1" in
        --limit) LIMIT="$2"; shift 2 ;;
        --since) SINCE="$2"; shift 2 ;;
        --quiet) QUIET=1; shift ;;
        --json)  JSON=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown flag $1" >&2; exit 2 ;;
    esac
done

# HERMES_SELF_TEST=1 hook — regex + report-shape fixtures.
if [ "${HERMES_SELF_TEST:-0}" = "1" ]; then
    ...
    exit 0
fi

# List last N merged PRs, sorted by mergedAt DESC.
prs=$(gh pr list --state merged --limit "$LIMIT" \
      --json number,body,mergedAt,mergeCommit \
      --jq '.[] | @json')

any_open=0
report='[]'
while IFS= read -r pr_json; do
    pr_num=$(echo "$pr_json" | jq -r '.number')
    body=$(echo "$pr_json" | jq -r '.body // ""')

    # Extract linked issue numbers with the same regex v3 uses.
    linked_nums=$(echo "$body" | grep -oiE '\b(close[sd]?|fix(es|ed)?|resolve[sd]?)\s+#[0-9]+' \
                  | grep -oE '[0-9]+' | sort -u)

    diagnostic="v3_ok"
    issues_json='[]'
    for iss in $linked_nums; do
        state=$(gh issue view "$iss" --json state --jq .state)
        issues_json=$(echo "$issues_json" | jq --arg n "$iss" --arg s "$state" \
                      '. + [{number: ($n | tonumber), state: $s}]')
        if [ "$state" = "OPEN" ]; then
            any_open=1
            diagnostic="issue_still_open"
        fi
    done

    # If any issue is open, correlate against workflow log for finer diagnostic.
    if [ "$diagnostic" = "issue_still_open" ]; then
        run_id=$(gh api "repos/ricardoafo/hermes/actions/workflows/merge-queue.yml/runs?per_page=50" \
                 --jq '[.workflow_runs[] | select(.display_title | contains("#'"$pr_num"'"))][0].id')
        if [ -n "$run_id" ] && [ "$run_id" != "null" ]; then
            log=$(gh run view "$run_id" --log 2>/dev/null || echo "")
            if echo "$log" | grep -q "closed linked issue"; then
                diagnostic="v3_ok_race_condition"
            elif echo "$log" | grep -q "failed to close linked issue"; then
                diagnostic="v3_fired_but_token_or_perm_bug"
            else
                diagnostic="v3_did_not_run"
            fi
        fi
    fi

    entry=$(jq -n --argjson pr "$pr_num" --argjson iss "$issues_json" \
            --arg diag "$diagnostic" \
            '{pr: $pr, linked_issues: $iss, diagnostic: $diag}')
    report=$(echo "$report" | jq --argjson e "$entry" '. + [$e]')

    if [ "$QUIET" = 0 ] && [ "$JSON" = 0 ]; then
        printf "#%s: %s (%d linked)\n" "$pr_num" "$diagnostic" \
               "$(echo "$issues_json" | jq 'length')"
    fi
done <<< "$prs"

if [ "$JSON" = 1 ]; then
    echo "$report" | jq .
fi

exit $any_open
```

## Test plan

1. `HERMES_SELF_TEST=1 bash scripts/dev/verify-auto-close.sh` — self-test exits 0.
2. Live run against real repo: `bash scripts/dev/verify-auto-close.sh --limit 10` — reports status of last 10 merges.
3. `bash scripts/dev/verify-auto-close.sh --json --limit 3 | jq 'length'` — returns 3.
4. `bash scripts/dev/pr-preflight.sh --render-body verify-auto-close-tool` — 17 checks green.

## Risks

- **R1** — `gh run view --log` is slow (2-4s per run). Mitigation: only query the log when the issue is OPEN (fast path when everything works). Full sweep at `--limit 10` costs at most 10 × 4s = 40s in the worst case (everything failed).
- **R2** — Workflow log retention is 90 days by default. Audits older than 90 days lose the diagnostic marker. Mitigation: report shows `v3_did_not_run` in that case; operator can distinguish from a real regression by checking merge date.
- **R3** — API rate limit at high `--limit N`. Mitigation: documented; default 10.

## Dependencies on other changes

- Predecessor: PR #262 (v3 body-regex + token split) — this tool reports on v3's success rate.
- Complementary: any future scheduled workflow that runs this hourly and alerts on failure.
