#!/usr/bin/env bash
# No-naive-datetime guard.
#
# Forbids naive datetime creation in the codebase per code-conventions §9
# ("UTC internally, Europe/Madrid at edges — no naive datetimes anywhere").
#
# Called by:
#   - .pre-commit-config.yaml `no-naive-datetime` hook (production)
#   - Pre-commit hook when THIS script changes (self-test)
#   - Manually: bash scripts/dev/no-naive-datetime.sh
#
# Env vars:
#   HERMES_SELF_TEST  — set to 1 to run built-in pass/fail cases and exit
#
# Patterns caught (Python regex):
#   datetime\.utcnow            — deprecated, returns naive
#   \bdate\.today\(\)           — returns naive date
#   datetime\.now\(\s*\)        — empty parens; datetime.now(tz=...) is fine
#
# Scope: src/hermes/**/*.py + tests/**/*.py
# Whitelist: src/hermes/infrastructure/time.py (defines the safe helpers).
#
# Exit codes:
#   0 — no violations (or self-test passed)
#   1 — violations found
#   2 — usage error

set -euo pipefail

WHITELIST_REGEX='^src/hermes/infrastructure/time\.py$'
PATTERNS=(
    'datetime\.utcnow'
    '\bdate\.today\(\)'
    'datetime\.now\(\s*\)'
)

# ────────────────────────────────────────────────────────────────
# scan_tree <root-dir>
# echoes offending "file:line:match" rows; exits 0 with empty output on clean.
# ────────────────────────────────────────────────────────────────
scan_tree() {
    local root="$1"
    local hits=()
    local pattern
    for pattern in "${PATTERNS[@]}"; do
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            # Strip leading "./" if present
            line="${line#./}"
            local path="${line%%:*}"
            if [[ "$path" =~ $WHITELIST_REGEX ]]; then
                continue
            fi
            hits+=("$line")
        done < <(grep -RInE "$pattern" "$root" --include='*.py' 2>/dev/null || true)
    done
    printf '%s\n' "${hits[@]}"
}

# ────────────────────────────────────────────────────────────────
# Self-test — synthetic pass + fail per pattern
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    failed=0
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT

    mkdir -p "$tmp/pass" "$tmp/fail"

    # PASS fixture — every allowed form
    cat > "$tmp/pass/ok.py" <<'EOF'
from datetime import datetime
from zoneinfo import ZoneInfo
_UTC = ZoneInfo("UTC")
def now():
    return datetime.now(tz=_UTC)
EOF
    result=$(scan_tree "$tmp/pass")
    if [[ -z "$result" ]]; then
        echo "PASS: tz-aware datetime.now(tz=...) not flagged"
    else
        echo "FAIL: tz-aware call was flagged: $result"; failed=1
    fi

    # FAIL fixtures — one per pattern
    cat > "$tmp/fail/bad_utcnow.py" <<'EOF'
from datetime import datetime
x = datetime.utcnow()
EOF
    cat > "$tmp/fail/bad_today.py" <<'EOF'
from datetime import date
d = date.today()
EOF
    cat > "$tmp/fail/bad_now_empty.py" <<'EOF'
from datetime import datetime
n = datetime.now()
EOF
    result=$(scan_tree "$tmp/fail")
    for expect in bad_utcnow.py bad_today.py bad_now_empty.py; do
        if echo "$result" | grep -q "$expect"; then
            echo "PASS: caught $expect"
        else
            echo "FAIL: did NOT catch $expect (result: $result)"; failed=1
        fi
    done

    if [[ "$failed" == 1 ]]; then
        echo ""
        echo "SELF-TEST FAILED"
        exit 1
    fi
    echo ""
    echo "All self-tests passed."
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Production path — scan src/hermes/ and tests/
# ────────────────────────────────────────────────────────────────
all_hits=""
for root in src/hermes tests; do
    [[ -d "$root" ]] || continue
    found=$(scan_tree "$root")
    if [[ -n "$found" ]]; then
        all_hits+="$found"$'\n'
    fi
done

if [[ -n "${all_hits// /}" && -n "${all_hits//$'\n'/}" ]]; then
    echo "::error::naive datetime usage detected (code-conventions §9)"
    echo "$all_hits"
    echo ""
    echo "Use hermes.infrastructure.time.now_utc() instead of datetime.now()/utcnow()/date.today()."
    exit 1
fi

echo "No naive datetime usage found."
