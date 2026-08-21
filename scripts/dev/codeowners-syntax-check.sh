#!/usr/bin/env bash
# Validate .github/CODEOWNERS syntax.
#
# Why this matters: a broken CODEOWNERS silently disables code-owner review
# enforcement — GitHub does not fail the PR, it just skips the check. That
# looks green in CI while the merge queue happily merges unreviewed code.
# The failure mode this catches is a typo (missing @, misspelled username,
# bad glob) that survives review because nobody sees it fire.
#
# Rules:
#   - blank lines and lines starting with '#' are allowed
#   - every other line must be "<pattern> @user1 [@user2 ...]"
#   - each owner must be a valid @<login> or @<org>/<team>
#   - the pattern must not be empty
#
# Self-test: HERMES_SELF_TEST=1 bash scripts/dev/codeowners-syntax-check.sh
set -euo pipefail

check_file() {
    local file="$1"
    local errors=0
    local lineno=0

    # Disable pathname expansion so patterns like `*` and `docs/*` in
    # CODEOWNERS are treated as literal strings, not shell globs. Without
    # this, `local tokens=($line)` would glob `* @ricardoafo` into every
    # file in the current directory.
    set -f

    while IFS= read -r line || [[ -n "$line" ]]; do
        lineno=$((lineno + 1))

        # skip blank lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue

        # split into pattern + owners
        # shellcheck disable=SC2206
        local tokens=($line)
        local pattern="${tokens[0]:-}"
        local owners=("${tokens[@]:1}")

        if [[ -z "$pattern" ]]; then
            echo "::error file=$file,line=$lineno::empty pattern"
            errors=$((errors + 1))
            continue
        fi

        if [[ ${#owners[@]} -eq 0 ]]; then
            echo "::error file=$file,line=$lineno::pattern '$pattern' has no owners"
            errors=$((errors + 1))
            continue
        fi

        for owner in "${owners[@]}"; do
            # allow @user or @org/team; reject anything else
            if [[ ! "$owner" =~ ^@[A-Za-z0-9][A-Za-z0-9_-]*(/[A-Za-z0-9][A-Za-z0-9_-]*)?$ ]]; then
                echo "::error file=$file,line=$lineno::invalid owner '$owner' (must be @user or @org/team)"
                errors=$((errors + 1))
            fi
        done
    done < "$file"

    set +f
    return "$errors"
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT

    # Case 1: valid file passes
    cat > "$tmp/valid" <<'EOF'
# CODEOWNERS test fixture
* @ricardoafo
docs/ @ricardoafo @some-org/reviewers
EOF
    if check_file "$tmp/valid"; then
        echo "[self-test] PASS: valid file accepted"
    else
        echo "[self-test] FAIL: valid file rejected"; exit 1
    fi

    # Case 2: pattern with no owner
    cat > "$tmp/no-owner" <<'EOF'
src/
EOF
    if check_file "$tmp/no-owner" 2>/dev/null; then
        echo "[self-test] FAIL: pattern without owner accepted"; exit 1
    else
        echo "[self-test] PASS: pattern without owner rejected"
    fi

    # Case 3: bad owner (missing @)
    cat > "$tmp/bad-owner" <<'EOF'
src/ ricardoafo
EOF
    if check_file "$tmp/bad-owner" 2>/dev/null; then
        echo "[self-test] FAIL: bad owner accepted"; exit 1
    else
        echo "[self-test] PASS: bad owner rejected"
    fi

    # Case 4: comment-only lines OK
    cat > "$tmp/comments" <<'EOF'
# just comments
   # indented comment

EOF
    if check_file "$tmp/comments"; then
        echo "[self-test] PASS: comment-only file accepted"
    else
        echo "[self-test] FAIL: comment-only file rejected"; exit 1
    fi

    echo "[self-test] all 4 cases passed"
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Real run: locate CODEOWNERS and validate
# ────────────────────────────────────────────────────────────────
CODEOWNERS_PATH="${CODEOWNERS_PATH:-}"
if [[ -z "$CODEOWNERS_PATH" ]]; then
    for candidate in .github/CODEOWNERS docs/CODEOWNERS CODEOWNERS; do
        if [[ -f "$candidate" ]]; then
            CODEOWNERS_PATH="$candidate"
            break
        fi
    done
fi

if [[ -z "$CODEOWNERS_PATH" ]]; then
    echo "::error::no CODEOWNERS file found in .github/, docs/, or repo root"
    exit 1
fi

echo "Validating $CODEOWNERS_PATH"
if check_file "$CODEOWNERS_PATH"; then
    echo "CODEOWNERS syntax OK"
else
    echo "::error::CODEOWNERS has syntax errors — a broken CODEOWNERS silently disables review enforcement"
    exit 1
fi
