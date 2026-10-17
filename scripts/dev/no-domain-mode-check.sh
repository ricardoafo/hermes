#!/usr/bin/env bash
# No-domain-mode-check guard.
#
# Forbids TradingMode branching inside the domain layer per code-conventions
# §11 ("Composition rule: TradingMode check happens ONCE, at the use-case
# entry. Domain functions never check mode.").
#
# Called by:
#   - .pre-commit-config.yaml `no-domain-mode-check` hook (production)
#   - Pre-commit hook when THIS script changes (self-test)
#   - Manually: bash scripts/dev/no-domain-mode-check.sh
#
# Env vars:
#   HERMES_SELF_TEST  — set to 1 to run built-in pass/fail cases and exit
#
# Patterns caught (extended regex):
#   \bif[[:space:]]+.*\bmode[[:space:]]*==       — `if mode ==` / `if self.mode ==`
#   \bmode[[:space:]]+is[[:space:]]+TradingMode  — `if mode is TradingMode.X`
#   \bTradingMode\.                              — any TradingMode enum access
#
# Scope: src/hermes/domain/**/*.py ONLY. (Use-case layer at hermes.application.*
# is EXPECTED to branch on mode — that IS the composition rule.)
#
# Exit codes:
#   0 — no violations (or self-test passed)
#   1 — violations found
#   2 — usage error

set -euo pipefail

PATTERNS=(
    '\bif[[:space:]]+.*\bmode[[:space:]]*=='
    '\bmode[[:space:]]+is[[:space:]]+TradingMode'
    '\bTradingMode\.'
)

# ────────────────────────────────────────────────────────────────
# scan_tree <root-dir>
# ────────────────────────────────────────────────────────────────
scan_tree() {
    local root="$1"
    local hits=()
    local pattern
    for pattern in "${PATTERNS[@]}"; do
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            line="${line#./}"
            hits+=("$line")
        done < <(grep -RInE "$pattern" "$root" --include='*.py' 2>/dev/null || true)
    done
    printf '%s\n' "${hits[@]}"
}

# ────────────────────────────────────────────────────────────────
# Self-test
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    failed=0
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT

    mkdir -p "$tmp/pass" "$tmp/fail"

    # PASS fixture — pure domain code, no mode branching
    cat > "$tmp/pass/decide.py" <<'EOF'
def decide(portfolio, target):
    return portfolio.diff(target)
EOF
    result=$(scan_tree "$tmp/pass")
    if [[ -z "$result" ]]; then
        echo "PASS: mode-free domain code not flagged"
    else
        echo "FAIL: mode-free code was flagged: $result"; failed=1
    fi

    # FAIL fixtures — one per pattern
    cat > "$tmp/fail/bad_if_eq.py" <<'EOF'
def run(mode):
    if mode == "live":
        pass
EOF
    cat > "$tmp/fail/bad_is_enum.py" <<'EOF'
def run(mode):
    if mode is TradingMode.LIVE:
        pass
EOF
    cat > "$tmp/fail/bad_enum_ref.py" <<'EOF'
def default_mode():
    return TradingMode.PAPER
EOF
    result=$(scan_tree "$tmp/fail")
    for expect in bad_if_eq.py bad_is_enum.py bad_enum_ref.py; do
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
# Production path — scan src/hermes/domain/ ONLY
# ────────────────────────────────────────────────────────────────
root="src/hermes/domain"
if [[ ! -d "$root" ]]; then
    echo "::warning::$root not found — skipping (nothing to scan)"
    exit 0
fi

domain_hits=$(scan_tree "$root")
if [[ -n "${domain_hits// /}" && -n "${domain_hits//$'\n'/}" ]]; then
    echo "::error::TradingMode branching detected in domain layer (code-conventions §11)"
    echo "$domain_hits"
    echo ""
    echo "Move mode-branching to hermes.application.*_uc — domain must be mode-agnostic."
    exit 1
fi

echo "No TradingMode usage in domain layer."
