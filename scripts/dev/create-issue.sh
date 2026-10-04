#!/usr/bin/env bash
# Mandatory wrapper around `gh issue create` for hermes.
#
# Bare `gh issue create` calls have twice slipped past discipline
# (2026-08-26 #245 — no milestone, no assignee). This wrapper enforces
# every metadata class the AGENTS.md § "On issue discipline" rule
# requires:
#
#   --title <str>            required
#   --milestone <str>        required
#   --assignee <str>         required (bot or human)
#   --body <str> | --body-file <path>   at least one required
#   --label type:<x>         required (drives conventional-commit + changelog class)
#   --label area:<x>         required (drives CODEOWNERS routing)
#   --label size:<x>         required (drives review-time estimate)
#
# Additional forwarded flags: --label (repeatable), --project, --web.
# Any other flag errors out — the allowlist keeps the wrapper contract
# auditable.
#
# Exit codes:
#   0 — issue created (or dry-run report emitted)
#   1 — gh not authenticated / repo lookup failed
#   2 — usage / missing metadata
#
# Env:
#   HERMES_ISSUE_DRY_RUN=1   print the `gh issue create` command and exit
#   HERMES_SELF_TEST=1       run fixture matrix; assert every missing-metadata
#                             case fails with exit 2 and produces no `gh` call
#
# Related:
#   openspec/changes/create-issue-wrapper/change.md
#   AGENTS.md § "On issue discipline"

set -uo pipefail

usage() {
    sed -n '2,32p' "$0" | sed 's/^# \?//'
}

# ────────────────────────────────────────────────────────────────
# Self-test harness. Runs entirely in dry-run mode — no `gh` calls.
# Asserts each missing-metadata case exits 2 and the fully-specified
# case exits 0.
# ────────────────────────────────────────────────────────────────
if [ "${HERMES_SELF_TEST:-0}" = "1" ]; then
    set -e
    script="$0"

    run_case() {
        local desc="$1"; shift
        local expected_exit="$1"; shift
        local actual=0
        # Unset HERMES_SELF_TEST in the child so it runs the real flag
        # parser, not another self-test round.
        env -u HERMES_SELF_TEST HERMES_ISSUE_DRY_RUN=1 \
            bash "$script" "$@" >/dev/null 2>&1 || actual=$?
        if [ "$actual" != "$expected_exit" ]; then
            echo "SELF-TEST FAIL: $desc — expected exit $expected_exit, got $actual" >&2
            exit 1
        fi
    }

    # Missing every required flag — usage error.
    run_case "no flags"                            2
    # Only --title — missing milestone.
    run_case "--title only"                        2  --title T
    # --title + --milestone — missing --assignee.
    run_case "title+milestone"                     2  --title T --milestone M
    # --title + --milestone + --assignee — missing --body.
    run_case "title+milestone+assignee no body"    2  --title T --milestone M --assignee A
    # ... + body but no labels.
    run_case "no labels"                           2  --title T --milestone M --assignee A --body B
    # ... + only type label (missing area + size).
    run_case "only type label"                     2  --title T --milestone M --assignee A --body B \
                                                     --label type:chore
    # ... + type + area (missing size).
    run_case "type+area no size"                   2  --title T --milestone M --assignee A --body B \
                                                     --label type:chore --label area:tooling
    # Fully specified.
    run_case "fully specified"                     0  --title T --milestone M --assignee A --body B \
                                                     --label type:chore --label area:tooling --label size:s

    # Unknown flag rejected.
    run_case "unknown flag"                        2  --title T --milestone M --assignee A --body B \
                                                     --label type:chore --label area:tooling --label size:s \
                                                     --recover

    echo "SELF-TEST PASS: all 9 metadata cases behave as specified."
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Flag parsing (allowlist).
# ────────────────────────────────────────────────────────────────
PASSTHROUGH=()
LABELS=()
declare -A SEEN=([--title]="" [--milestone]="" [--assignee]="" [--body]="" [--body-file]="")

while [ $# -gt 0 ]; do
    case "$1" in
        --title|--milestone|--assignee|--project)
            [ $# -ge 2 ] || { echo "ERROR: $1 requires a value" >&2; exit 2; }
            SEEN["$1"]="$2"
            PASSTHROUGH+=("$1" "$2")
            shift 2 ;;
        --body|--body-file)
            [ $# -ge 2 ] || { echo "ERROR: $1 requires a value" >&2; exit 2; }
            SEEN["$1"]="$2"
            PASSTHROUGH+=("$1" "$2")
            shift 2 ;;
        --label)
            [ $# -ge 2 ] || { echo "ERROR: --label requires a value" >&2; exit 2; }
            LABELS+=("$2")
            PASSTHROUGH+=("$1" "$2")
            shift 2 ;;
        --web)
            PASSTHROUGH+=("$1")
            shift ;;
        -h|--help)
            usage
            exit 0 ;;
        *)
            echo "ERROR: unknown flag '$1' (allowlist: --title --body --body-file --milestone --assignee --label --project --web)" >&2
            exit 2 ;;
    esac
done

# ────────────────────────────────────────────────────────────────
# Required-metadata checks.
# ────────────────────────────────────────────────────────────────
for req in --title --milestone --assignee; do
    if [ -z "${SEEN[$req]}" ]; then
        echo "ERROR: $req is required (see AGENTS.md § 'On issue discipline')" >&2
        exit 2
    fi
done
if [ -z "${SEEN[--body]}" ] && [ -z "${SEEN[--body-file]}" ]; then
    echo "ERROR: --body or --body-file is required" >&2
    exit 2
fi

# ────────────────────────────────────────────────────────────────
# Label class enforcement — need one of each of type / area / size.
# ────────────────────────────────────────────────────────────────
has_type=0; has_area=0; has_size=0
for l in "${LABELS[@]:-}"; do
    case "$l" in
        type:*) has_type=1 ;;
        area:*) has_area=1 ;;
        size:*) has_size=1 ;;
    esac
done
[ "$has_type" = 1 ] || { echo "ERROR: --label type:... required (drives conventional-commit + changelog class)" >&2; exit 2; }
[ "$has_area" = 1 ] || { echo "ERROR: --label area:... required (drives CODEOWNERS routing)" >&2; exit 2; }
[ "$has_size" = 1 ] || { echo "ERROR: --label size:... required (drives review-time estimate)" >&2; exit 2; }

# ────────────────────────────────────────────────────────────────
# Dry-run or execute.
# ────────────────────────────────────────────────────────────────
if [ "${HERMES_ISSUE_DRY_RUN:-0}" = "1" ]; then
    printf 'DRY: gh issue create'
    for arg in "${PASSTHROUGH[@]}"; do printf ' %q' "$arg"; done
    printf '\n'
    exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: 'gh' CLI required" >&2
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: 'gh' is not authenticated — run 'gh auth login'" >&2
    exit 1
fi

exec gh issue create "${PASSTHROUGH[@]}"
