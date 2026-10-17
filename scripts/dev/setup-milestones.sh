#!/usr/bin/env bash
# Idempotent milestone bootstrap for ricardoafo/hermes.
# Milestones map to Phases per the roadmap (see ADR-003 §00 and dev-workflow.md).
# gh CLI does not have `milestone create` — uses gh api directly.

set -euo pipefail

REPO="${REPO:-ricardoafo/hermes}"

create_or_update_milestone() {
  local title="$1"
  local description="$2"
  local existing_number
  existing_number=$(gh api "repos/$REPO/milestones?state=all" \
    --jq ".[] | select(.title == \"$title\") | .number" | head -1)

  if [[ -n "$existing_number" ]]; then
    gh api -X PATCH "repos/$REPO/milestones/$existing_number" \
      -f description="$description" \
      -f state=open \
      >/dev/null
    echo "  ✓ updated: $title (#$existing_number)"
  else
    gh api -X POST "repos/$REPO/milestones" \
      -f title="$title" \
      -f state=open \
      -f description="$description" \
      >/dev/null
    echo "  ✓ created: $title"
  fi
}

echo "Setting up milestones for $REPO"
echo ""

create_or_update_milestone \
  "Phase 0 — Scaffold" \
  "pyproject.toml, hexagonal src/hermes/ skeleton, first CI workflows (ADR-006/007/010), pytest+hypothesis+syrupy+mutmut, ruff+mypy, structlog scaffold, safety+pip-audit, Renovate, docs/guides/*+docs/runbook/+docs/decisions/ stubs. No trading logic yet."

create_or_update_milestone \
  "Phase 1 — Simulation" \
  "Paper-trading engine, FIFO lot ledger, backtest harness with historical klines, deterministic price replay, signal engines in strategy pattern. All dry-run; no real orders."

create_or_update_milestone \
  "Phase 2 — Shadow" \
  "Tiny real-capital on Binance ARG-KYC, live order flow with typed confirmations, execution-logging (arrival_price/fill_price/delta_ms), 90-day observation window, failover drill against Kraken cold backup, first live tax lot-consumptions in DB."

create_or_update_milestone \
  "Phase 3 — Live" \
  "Full production trading, LLM Supervisor Layer (MCP read-only per ADR-001/003 §08), scheduled scans + notifications, monthly reconciliation, Modelo 100/721 filing pipeline, CVE-response runbook exercised."

echo ""
echo "Done. Milestones are idempotent — safe to re-run."
