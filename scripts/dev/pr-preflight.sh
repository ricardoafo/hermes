#!/usr/bin/env bash
# PR pre-flight — mirror the exact suite CI runs, locally, before push.
#
# Prevents "pushed, then CI red" round-trips. Every check here is a check
# CI runs on the PR; if any fails locally, CI will fail too.
#
# Invocation:
#   bash scripts/dev/pr-preflight.sh                          # normal pre-push
#   bash scripts/dev/pr-preflight.sh --body FILE              # + PR body lint
#   bash scripts/dev/pr-preflight.sh --render-body <change>   # render + lint
#   HERMES_SELF_TEST=1 bash scripts/dev/pr-preflight.sh       # sanity check
#
# Wired to git-push via `.pre-commit-config.yaml` `stages: [pre-push]` hook.
# One-time install:
#   pre-commit install --hook-type pre-push
#
# Exit codes:
#   0 — every check passed
#   1 — at least one check failed
#   2 — usage error

set -uo pipefail

# ────────────────────────────────────────────────────────────────
# Argument parsing
# ────────────────────────────────────────────────────────────────
BODY_FILE=""
RENDER_CHANGE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --body)
            BODY_FILE="${2:-}"
            if [[ -z "$BODY_FILE" || ! -f "$BODY_FILE" ]]; then
                echo "ERROR: --body requires a readable file path" >&2
                exit 2
            fi
            shift 2
            ;;
        --render-body)
            RENDER_CHANGE="${2:-}"
            if [[ -z "$RENDER_CHANGE" ]]; then
                echo "ERROR: --render-body requires a change name" >&2
                exit 2
            fi
            shift 2
            ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

# Render mode: generate body from openspec/changes/<name>/change.md +
# issue.md, then treat as --body. Fails early if change.md or issue.md
# is missing. Auto-derives --branch from the current git branch (unless
# on main) so render-pr-body.sh can fall back to `git show origin/<ref>`
# if the local file was removed by the "delete-local-after-open" step
# of the Code Owner's PR discipline.
if [[ -n "$RENDER_CHANGE" ]]; then
    BODY_FILE=$(mktemp -t pr-body.XXXXXX.md)
    echo "▶ Rendering PR body for change '$RENDER_CHANGE'"

    # Determine branch for the remote-fallback arg. Skip on main.
    current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    render_args=("$RENDER_CHANGE" -o "$BODY_FILE")
    if [[ -n "$current_branch" && "$current_branch" != "main" ]]; then
        render_args+=(--branch "$current_branch")
    fi

    if ! bash scripts/dev/render-pr-body.sh "${render_args[@]}"; then
        echo "ERROR: render-pr-body.sh failed for '$RENDER_CHANGE'" >&2
        exit 1
    fi
    echo "  ✓ body rendered to $BODY_FILE"
fi

# ────────────────────────────────────────────────────────────────
# Self-test — validate the script's own logic without side-effects
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-}" == "1" ]]; then
    # Verify all mandatory tools are declared and the script parses.
    # Real check-invocations happen only in the non-self-test path.
    for cmd in ruff mypy lint-imports pytest pytest-timeout markdownlint-cli2 git radon vulture coverage-check; do
        if ! grep -q "\\b${cmd}\\b" "$0"; then
            echo "SELF-TEST FAIL: script does not reference '${cmd}'" >&2
            exit 1
        fi
    done
    echo "SELF-TEST PASS: pr-preflight.sh declares expected tools."
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Result tracking
#
# Total check count is CONDITIONAL, by design:
#   - 13 checks always run (checks 1-12 plus per-package coverage).
#   - +1 check runs ONLY when --body FILE or --render-body <change> is
#     given (the PR body lint at the bottom).
# Total = 13 unconditional + 1 optional. Not drift — different modes.
# See summary line for the exact per-invocation breakdown.
# ────────────────────────────────────────────────────────────────
FAILED=()
CHECK_COUNT=0

run_check() {
    local name="$1"
    shift
    CHECK_COUNT=$((CHECK_COUNT + 1))
    printf "▶ %s\n" "$name"
    if "$@"; then
        printf "  ✓ pass\n"
    else
        printf "  ✗ FAIL\n"
        FAILED+=("$name")
    fi
}

# ────────────────────────────────────────────────────────────────
# Preconditions — the environment must be usable
#
# uv may not be on PATH inside subshells (pre-commit spawns without
# env inheritance on Windows). Probe common install locations before
# giving up.
# ────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    for candidate in \
        "$HOME/AppData/Roaming/Python/Scripts/uv.exe" \
        "$HOME/.local/bin/uv" \
        "$HOME/.cargo/bin/uv" \
        "/usr/local/bin/uv"
    do
        if [[ -x "$candidate" ]]; then
            export PATH="$(dirname "$candidate"):$PATH"
            break
        fi
    done
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found on PATH or in common install locations." >&2
    echo "  Windows: uv typically lives at ~/AppData/Roaming/Python/Scripts/uv.exe" >&2
    echo "  Linux/macOS: ~/.local/bin/uv or ~/.cargo/bin/uv" >&2
    echo "  Install: https://github.com/astral-sh/uv" >&2
    exit 2
fi

# ────────────────────────────────────────────────────────────────
# Checks — same commands CI runs, no more no less
# ────────────────────────────────────────────────────────────────

# MUST MATCH create-pr.sh:77 — same TITLE_REGEX; single source of truth.
# If you change one, change both AND update this comment's line number.
TITLE_REGEX='^[a-z]+(\(.+\))?!?:[[:space:]]+[a-z]'

# Check: PR title semantic (conventional-commit + lowercase subject).
# Mirrors amannn/action-semantic-pull-request@v6 subjectPattern. A rebase
# or amend can leave HEAD as a fixup / uppercase subject that create-pr.sh
# accepted at open time but CI rejects, forcing a close+reopen cycle
# (drift observed 2026-08-25 on 'PAT-authenticate' — see #215 Tier-2 defer).
# Bypass with SKIP_TITLE_CHECK=1 (documented for rebase-in-progress paths).
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

# 1. Branch is up-to-date with origin/main (rebase check)
check_base_current() {
    git fetch origin main --quiet || return 1
    git merge-base --is-ancestor origin/main HEAD
}
run_check "Base up-to-date with origin/main" check_base_current

# 2. Ruff format on FULL tree (CI runs `ruff format --check .`)
run_check "ruff format --check ."     uv run ruff format --check .

# 3. Ruff lint on FULL tree
run_check "ruff check ."              uv run ruff check .

# 4. Mypy strict on src/
run_check "mypy --strict src/hermes"  uv run mypy --strict src/hermes

# 5. Import-linter contracts
run_check "lint-imports"              uv run lint-imports

# 6. Pip-audit (CVE scan) — --skip-editable to ignore local hermes package
run_check "pip-audit --skip-editable" uv run pip-audit --skip-editable

# 7. Radon complexity — matches CI quality matrix leg "complexity".
#    NOTE: radon cc --min B prints violations but exits 0 regardless; kept
#    as-is to mirror CI exactly (informational-only gate). Strengthening the
#    exit-code semantic is out of scope for this drift-closure change.
run_check "radon (complexity)"        uv run radon cc --min B src/

# 8. Vulture dead-code — matches CI quality matrix leg "deadcode".
#    Reads config from [tool.vulture] in pyproject.toml (min_confidence=80).
run_check "vulture (dead code)"       uv run vulture src/

# 9. Pytest — verbatim CI (unit job): tests/unit/ with branch coverage,
#    emits coverage.json which check #14 (coverage-check.py) reads.
#    pytest-timeout (60s default, thread method) is configured in
#    pyproject.toml [tool.pytest.ini_options]. A test that hangs past the
#    cap fails LOUDLY with a stack trace instead of silently draining CI
#    time — closes the drift from the wave-5 sub-agent hang on a klines
#    fetcher rate-limiter float-drift infinite loop.
# SKIP_PYTEST=1 skips checks #9 (pytest) AND #14 (per-package coverage,
# which depends on coverage.json produced by pytest). Escape hatch for
# unblocking PRs when a known-flake in an unrelated area of the tree
# fails deterministically-red but is NOT related to the PR under review.
# Log a reason when using: `SKIP_PYTEST=1  # tracking bug #NNN`.
check_pytest_gated() {
    if [[ "${SKIP_PYTEST:-0}" == "1" ]]; then
        echo "  WARN: pytest skipped via SKIP_PYTEST=1 — known-flake bypass" >&2
        return 0
    fi
    uv run pytest tests/unit/ --cov=src/hermes --cov-branch --cov-report=json --cov-report=term
}
run_check "pytest tests/unit/ (CI-verbatim)" check_pytest_gated

# 14. Per-package coverage thresholds — matches CI unit job's
#     `coverage-check.py --bootstrap` step. Depends on coverage.json
#     produced by check #9. `--bootstrap` accepts empty Phase 0 layers.
check_coverage_gated() {
    if [[ "${SKIP_PYTEST:-0}" == "1" ]]; then
        echo "  WARN: coverage skipped (SKIP_PYTEST=1 also skips this)" >&2
        return 0
    fi
    uv run python scripts/dev/coverage-check.py --bootstrap
}
run_check "coverage-check.py (per-package thresholds)" check_coverage_gated

# 10. Markdownlint on FULL tree (CI runs the same glob)
run_check "markdownlint-cli2 (full tree)" \
    markdownlint-cli2 "**/*.md" "#node_modules" "#.venv"

# 11. Guides index up-to-date (CI regenerates and diffs)
check_guides_index() {
    bash scripts/dev/regenerate-guides-index.sh >/dev/null 2>&1 || return 1
    git diff --exit-code -- docs/guides/README.md >/dev/null 2>&1
}
run_check "Guides index current" check_guides_index

# 12. CHANGELOG check — infers PR title's conventional-commit type from
#     the LATEST commit on the branch (matches what the eventual squash
#     commit will inherit — squash-merge uses the PR title, which is
#     conventionally the last-committed message).
#     Skipped when the branch has no commits ahead of origin/main, or
#     when the inferred type is docs/chore/ci/build/test/revert (which
#     changelog-check.sh itself skips anyway).
check_changelog_if_applicable() {
    # Latest commit's subject line — the type prefix drives the check.
    local latest_subject
    latest_subject=$(git log -1 --format=%s HEAD 2>/dev/null)
    if [[ -z "$latest_subject" ]]; then
        return 0  # empty branch — nothing to check
    fi

    # Only feat/fix/refactor/perf require a CHANGELOG entry.
    if ! [[ "$latest_subject" =~ ^(feat|fix|refactor|perf)(\(.+\))?(!)?: ]]; then
        return 0  # docs/chore/ci/build/test/revert — skip
    fi

    # Base SHA for the diff — origin/main HEAD as of last fetch.
    # (base-current check already ran + refreshed origin/main.)
    local base_sha head_sha
    base_sha=$(git rev-parse origin/main 2>/dev/null)
    head_sha=$(git rev-parse HEAD)
    if [[ "$base_sha" == "$head_sha" ]]; then
        return 0  # branch at main — no diff to check
    fi

    PR_TITLE="$latest_subject" BASE_SHA="$base_sha" HEAD_SHA="$head_sha" \
        bash scripts/dev/changelog-check.sh >/dev/null 2>&1
}
run_check "CHANGELOG check (inferred from latest commit)" check_changelog_if_applicable

# 13. Openspec change spec present when the PR's conventional-commit type
#     implies a material change (feat|refactor|perf). Coarse heuristic
#     — false positives (small feat) bypass via SKIP_OPENSPEC_CHECK=1
#     with a logged reason. False negatives (large fix) caught in code
#     review. See #194 for the DoR/DoD rationale that motivates this.
# Check: tracking-issue project assignment (opt-in).
#
# For every openspec change modified on this branch, resolve its
# tracking issue via issue.md and verify the issue is attached to at
# least one GitHub Project. Ship-validation gap surfaced on #242 —
# operator caught PR #242 slipping through without a project attached.
#
# OPT-IN via HERMES_ENFORCE_PROJECT_CHECK=1. Default OFF because
# until every open tracking issue is attached to a project AND local
# operators have `read:project` scope, this check would fail-hard on
# every current tracking issue (`projectItems` returns `[]` both when
# no project is attached AND when the scope is missing — the two
# cases are indistinguishable to the caller). Flip the env var on
# once open issues are bulk-attached and the scope is granted.
#
# Bypass (when opt-in is on): SKIP_PROJECT_CHECK=1.
check_pr_project_assigned() {
    if [[ "${HERMES_ENFORCE_PROJECT_CHECK:-0}" != "1" ]]; then
        echo "  SKIP: enforcement OFF (set HERMES_ENFORCE_PROJECT_CHECK=1 to enable)" >&2
        return 0
    fi
    if [[ "${SKIP_PROJECT_CHECK:-0}" == "1" ]]; then
        echo "  WARN: project check skipped via SKIP_PROJECT_CHECK=1" >&2
        return 0
    fi
    local changes
    changes=$(git diff --name-only origin/main..HEAD -- 'openspec/changes/*/change.md' 2>/dev/null)
    if [[ -z "$changes" ]]; then
        # No openspec change on this branch — nothing to check.
        return 0
    fi

    local any_fail=0
    while IFS= read -r cf; do
        [[ -z "$cf" ]] && continue
        local change_dir issue_md issue_num count
        change_dir=$(dirname "$cf")
        issue_md="$change_dir/issue.md"
        [[ -f "$issue_md" ]] || continue
        issue_num=$(grep -oE '^issue:[[:space:]]*[0-9]+' "$issue_md" \
                    | grep -oE '[0-9]+' | head -1)
        [[ -z "$issue_num" ]] && continue

        count=$(gh issue view "$issue_num" --json projectItems \
                    --jq '.projectItems | length' 2>&1)
        if [[ "$count" == *INSUFFICIENT_SCOPES* ]] || [[ "$count" == *"required scopes"* ]]; then
            echo "  WARN: read:project scope missing — cannot verify project attachment for #$issue_num" >&2
            continue
        fi
        if ! [[ "$count" =~ ^[0-9]+$ ]]; then
            echo "  WARN: unexpected gh output for #$issue_num: ${count:0:120}" >&2
            continue
        fi
        if [[ "$count" -eq 0 ]]; then
            {
                echo "ERROR: tracking issue #$issue_num for change '$(basename "$change_dir")' has no project attached."
                echo "  Attach via the GitHub web UI (Projects tab on the issue) or:"
                echo "    gh api graphql -f query='mutation { addProjectV2ItemById(input: {projectId: \"...\", contentId: \"...\"}) { item { id } } }'"
                echo "  Bypass (with cause): SKIP_PROJECT_CHECK=1 bash scripts/dev/pr-preflight.sh"
            } >&2
            any_fail=1
        fi
    done <<< "$changes"

    return $any_fail
}
run_check "Tracking-issue project assignment (opt-in)" check_pr_project_assigned

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

# 13. Optional — PR body lint (only when --body FILE given)
if [[ -n "$BODY_FILE" ]]; then
    check_pr_body() {
        PR_BODY="$(cat "$BODY_FILE")" bash scripts/dev/pr-body-lint.sh
    }
    run_check "PR body lint (--body $BODY_FILE)" check_pr_body
fi

# ────────────────────────────────────────────────────────────────
# Summary
#
# The check count depends on invocation flags (see "Result tracking" comment
# above). The mode string makes the count discoverable so operators do not
# mistake it for silent drift between runs (drift-eval 2026-08-22, wave 3).
# ────────────────────────────────────────────────────────────────
if [[ -n "$BODY_FILE" ]]; then
    MODE_DESC="with PR body lint"
else
    MODE_DESC="without PR body lint — pass --body FILE or --render-body <change> to add it"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo "✓ ALL ${CHECK_COUNT} CHECKS PASS (${MODE_DESC}) — safe to push."
    exit 0
else
    echo "✗ ${#FAILED[@]} of ${CHECK_COUNT} CHECKS FAILED (${MODE_DESC}):"
    printf '   - %s\n' "${FAILED[@]}"
    echo ""
    echo "Push blocked. Fix the failures above, then re-run:"
    echo "  bash scripts/dev/pr-preflight.sh"
    exit 1
fi
