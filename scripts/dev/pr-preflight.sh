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
# is missing.
if [[ -n "$RENDER_CHANGE" ]]; then
    BODY_FILE=$(mktemp -t pr-body.XXXXXX.md)
    echo "▶ Rendering PR body for change '$RENDER_CHANGE'"
    if ! bash scripts/dev/render-pr-body.sh "$RENDER_CHANGE" -o "$BODY_FILE"; then
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
    for cmd in ruff mypy lint-imports pytest markdownlint-cli2 git radon vulture; do
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

# 9. Pytest (should be fast in Phase 0 — one test)
run_check "pytest tests/"             uv run pytest tests/ -q

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

# 13. Optional — PR body lint (only when --body FILE given)
if [[ -n "$BODY_FILE" ]]; then
    check_pr_body() {
        PR_BODY="$(cat "$BODY_FILE")" bash scripts/dev/pr-body-lint.sh
    }
    run_check "PR body lint (--body $BODY_FILE)" check_pr_body
fi

# ────────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo "✓ ALL ${CHECK_COUNT} CHECKS PASS — safe to push."
    exit 0
else
    echo "✗ ${#FAILED[@]} of ${CHECK_COUNT} CHECKS FAILED:"
    printf '   - %s\n' "${FAILED[@]}"
    echo ""
    echo "Push blocked. Fix the failures above, then re-run:"
    echo "  bash scripts/dev/pr-preflight.sh"
    exit 1
fi
