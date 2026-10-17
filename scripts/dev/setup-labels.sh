#!/usr/bin/env bash
# Idempotent label bootstrap for ricardoafo/hermes.
# Re-runnable: uses --force to update color/description on existing labels.
# Required: gh CLI authenticated as ricardoafo (or any admin/write collaborator).

set -euo pipefail

REPO="${REPO:-ricardoafo/hermes}"

create_label() {
  local name="$1"
  local color="$2"
  local description="$3"
  gh label create "$name" \
    --repo "$REPO" \
    --color "$color" \
    --description "$description" \
    --force \
    >/dev/null
  echo "  ✓ $name"
}

echo "Setting up labels for $REPO"
echo ""
echo "type:* (Conventional Commit types)"
create_label "type:feat"     "0e8a16" "New capability, user- or API-visible"
create_label "type:fix"      "d73a4a" "Bug fix in existing behavior"
create_label "type:docs"     "0075ca" "Documentation-only change"
create_label "type:chore"    "cccccc" "Tooling, deps, meta, no runtime impact"
create_label "type:refactor" "c5def5" "Behavior-preserving internal change"
create_label "type:test"     "fbca04" "Test additions or fixes only"
create_label "type:perf"     "6f42c1" "Performance improvement (measured)"
create_label "type:build"    "1d76db" "Build system, packaging"
create_label "type:ci"       "1d76db" "CI workflow change"
create_label "type:revert"   "333333" "Reverts a prior commit"

echo ""
echo "sev-* (severity per docs/guides/severity-rubric.md)"
create_label "sev-1" "b60205" "Critical: security/LOPDGDD/data loss/auth bypass. Blocks release+merge. Fix in 24h."
create_label "sev-2" "d93f0b" "High: feature broken, wrong legal output. Blocks merge. Fix in 72h."
create_label "sev-3" "fbca04" "Medium: degraded experience, non-critical wrong data. Fix in 7d."
create_label "sev-4" "c2e0c6" "Low: cosmetic, copy, minor visual. Next sprint."

echo ""
echo "p* (priority)"
create_label "p1" "d93f0b" "Priority: this week"
create_label "p2" "fbca04" "Priority: this month"
create_label "p3" "c2e0c6" "Priority: backlog"
create_label "merge-queue:hand-merge" "fbca04" "The merge queue cannot merge this PR itself; merge it by hand"
create_label "merge-queue:gates-failed" "b60205" "The merge queue gates failed on this head; push a fix or remove the label to retry"
create_label "merge-queue:alerts" "5319e7" "The one issue where the merge queue posts every red head"

echo ""
echo "area:* (code area — expand as codebase grows)"
create_label "area:domain"      "5319e7" "Pure business logic (hexagonal domain layer)"
create_label "area:adapters"    "5319e7" "Exchange, persistence, notification adapters"
create_label "area:cli"         "5319e7" "CLI + TUI (Typer / Rich / Textual)"
create_label "area:config"      "5319e7" "pydantic-settings, config schema, env"
create_label "area:docs"        "5319e7" "ADRs, guides, runbook, decisions journal"
create_label "area:tooling"     "5319e7" "CI, pre-commit, lint, deps, packaging"
create_label "area:security"    "5319e7" "Secrets, auth, LOPDGDD, supply chain"

echo ""
echo "size:* (review effort estimate)"
create_label "size:s" "c2e0c6" "≤100 LOC, ≤5 files. Review in ~5min."
create_label "size:m" "fbca04" "≤500 LOC, ≤20 files. Review in ~15min."
create_label "size:l" "d93f0b" "≤1500 LOC, ≤40 files. Review in ~45min. Consider splitting."

echo ""
echo "Done. Labels are idempotent — safe to re-run."
