#!/usr/bin/env bash
# Check a contributor's local toolchain against the versions CI pins.
#
# Usage:
#   bash scripts/dev/doctor.sh            # one line per check, exit 1 on any FAIL
#   HERMES_SELF_TEST=1 bash scripts/dev/doctor.sh
#
# "Passed locally, failed in CI" usually means a different tool version
# or a missing git hook. Each pin is read from the file CI itself uses,
# so this script never carries a second copy that can drift:
#   uv                  scripts/deploy/vps_bootstrap.sh  UV_VERSION
#   markdownlint-cli2   scripts/dev/gh-runner/Dockerfile MARKDOWNLINT_VERSION
#   gitleaks            scripts/dev/gh-runner/Dockerfile GITLEAKS_VERSION
#   project Python      pyproject.toml requires-python (via `uv run`)
#
# Severity: FAIL when a mismatch changes what a check decides (uv, the
# project Python, a missing hook), WARN when results can differ only at
# the edges (gitleaks rules, markdownlint, git identity).
#
# Exit codes: 0 no FAIL (WARNs allowed), 1 at least one FAIL.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# version_ge <have> <want>: true when have >= want (dotted numbers).
version_ge() {
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

# pin_from <file> <VAR>: the value of `VAR=...` or `ARG VAR=...` in file.
pin_from() {
    sed -n -E "s/^(ARG )?$2=([^[:space:]]+).*/\2/p" "$1" | head -n1
}

# first_version <text>: the first dotted version number in text.
first_version() {
    grep -oE '[0-9]+(\.[0-9]+)+' <<< "$1" | head -n1
}

fails=0
report() {
    local level=$1 what=$2 detail=$3
    printf '%-5s %-22s %s\n' "$level" "$what" "$detail"
    [[ "$level" == FAIL ]] && fails=$((fails + 1))
    return 0
}

check_exact() {
    local level=$1 what=$2 want=$3 have=$4
    if [[ -z "$have" ]]; then
        report "$level" "$what" "not found (want $want)"
    elif [[ "$have" == "$want" ]]; then
        report OK "$what" "$have"
    else
        report "$level" "$what" "$have, CI pins $want"
    fi
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    expect() {
        local desc=$1 want=$2
        shift 2
        local got=no
        "$@" && got=yes
        [[ "$got" == "$want" ]] || { echo "SELF-TEST FAIL: $desc"; fail=1; }
    }
    expect "24.14.0 >= 20" yes version_ge 24.14.0 20
    expect "3.11.9 >= 3.11" yes version_ge 3.11.9 3.11
    expect "3.10.4 < 3.11" no version_ge 3.10.4 3.11
    expect "0.12.10 >= 0.12.5 (numeric, not lexical)" yes version_ge 0.12.10 0.12.5
    tmp=$(mktemp)
    printf 'ARG UV_VERSION=0.12.5\nARG GITLEAKS_VERSION=8.21.2 # pinned\nUV_PIN=x\n' > "$tmp"
    [[ "$(pin_from "$tmp" GITLEAKS_VERSION)" == 8.21.2 ]] || { echo "SELF-TEST FAIL: pin_from ARG"; fail=1; }
    [[ "$(pin_from "$tmp" UV_VERSION)" == 0.12.5 ]] || { echo "SELF-TEST FAIL: pin_from first match"; fail=1; }
    rm -f "$tmp"
    [[ "$(first_version 'uv 0.12.5 (210d1f678 2026-08-14)')" == 0.12.5 ]] \
        || { echo "SELF-TEST FAIL: first_version"; fail=1; }
    [[ "$fail" == 0 ]] && echo "SELF-TEST PASS: doctor.sh helpers handle 7 fixtures correctly."
    exit "$fail"
fi

cd "$REPO_ROOT" || exit 1
dockerfile=scripts/dev/gh-runner/Dockerfile

check_exact FAIL uv "$(pin_from scripts/deploy/vps_bootstrap.sh UV_VERSION)" \
    "$(first_version "$(uv --version 2>/dev/null)")"

py=$(first_version "$(uv run --quiet python --version 2>/dev/null)")
if [[ "$py" == 3.14.* ]]; then
    report OK "project Python" "$py"
else
    report FAIL "project Python" "${py:-not found}, pyproject requires 3.14 (uv python install 3.14)"
fi

sys_py=$(first_version "$(python3 --version 2>/dev/null)")
if [[ -n "$sys_py" ]] && version_ge "$sys_py" 3.11; then
    report OK "python3 (scripts)" "$sys_py"
else
    report FAIL "python3 (scripts)" "${sys_py:-not found}; the dev scripts need 3.11+ (tomllib)"
fi

node_v=$(first_version "$(node --version 2>/dev/null)")
if [[ -n "$node_v" ]] && version_ge "$node_v" 20; then
    report OK node "$node_v"
else
    report FAIL node "${node_v:-not found}; tools/merge tests need Node 20+"
fi

check_exact WARN markdownlint-cli2 "$(pin_from "$dockerfile" MARKDOWNLINT_VERSION)" \
    "$(first_version "$(markdownlint-cli2 --version 2>/dev/null | head -n1)")"
check_exact WARN gitleaks "$(pin_from "$dockerfile" GITLEAKS_VERSION)" \
    "$(first_version "$(gitleaks version 2>/dev/null)")"

hooks_dir=$(git config --get core.hooksPath || echo "$(git rev-parse --git-common-dir)/hooks")
for hook in pre-commit pre-push commit-msg; do
    if [[ -x "$hooks_dir/$hook" ]]; then
        report OK "git hook $hook" "installed"
    else
        report FAIL "git hook $hook" "missing: pre-commit install --hook-type $hook"
    fi
done

identity=$(git config --get user.name || true)
if [[ "$identity" == rafo-claude-bot ]]; then
    report OK "git identity" "ai-bot"
else
    report WARN "git identity" "${identity:-unset}; agents commit as the ai-bot (AGENTS.md rule 13)"
fi

echo ""
if [[ "$fails" -gt 0 ]]; then
    echo "$fails check(s) FAILED: fix them before trusting a local pass."
    exit 1
fi
echo "Toolchain matches CI."
