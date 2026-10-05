#!/usr/bin/env bash
# Pre-commit hook: scan staged files for likely credentials before
# they reach the git object store.
#
# Rationale: gitleaks-in-CI catches secrets AFTER push. This runs on
# every `git commit` locally, blocking the commit if it detects
# likely-credential patterns in staged content. No window where a
# leaked secret exists on origin, not even briefly.
#
# Uses gitleaks in 'protect' mode (staged files only, no full history
# scan). If gitleaks isn't installed, prints a LOUD warning and
# exits 0 (soft-fail — CI will catch it, but we announce the gap).

set -euo pipefail

# ────────────────────────────────────────────────────────────────
# Check gitleaks is available (pre-baked in runner, may or may not
# be on operator's laptop depending on setup)
# ────────────────────────────────────────────────────────────────
if ! command -v gitleaks >/dev/null 2>&1; then
    echo ""
    echo "!!  gitleaks NOT INSTALLED on this machine"
    echo "!!  Pre-commit secrets scan SKIPPED — commit allowed, CI will catch."
    echo "!!  Install locally with: curl -fsSL https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz | tar -xz -C \$HOME/.local/bin gitleaks"
    echo "!!  (or the equivalent for macOS / Windows)"
    echo ""
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Scan staged files
# ────────────────────────────────────────────────────────────────
if gitleaks protect --staged --redact --no-banner --verbose; then
    exit 0
fi

echo ""
echo "!!  SECRET DETECTED IN STAGED CHANGES"
echo "!!  Commit BLOCKED. If this is a false positive, add an inline"
echo "!!  '# gitleaks:allow' comment on the same line as the flagged"
echo "!!  value, then re-stage and retry."
echo ""
exit 1
