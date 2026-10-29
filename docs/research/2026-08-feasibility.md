# Feasibility digest — 2026-08

**Date**: 2026-08-21 (bootstrap placeholder)
**Author**: Maintainer
**Fed decisions**: [ADR-001](../architecture/adr-001-llm-as-supervisor.md),
[ADR-005](../architecture/adr-005-ai-ml-support-boundary.md)
**Sources**: internal engram observations from research tracks 1-7
(2026-08-20 session — see engram topic keys
`hermes/research/track-1-vps` through `hermes/research/track-7-pr-checks`)

## Purpose of this file

This file is a **placeholder** referenced by ADR-001 (LLM as supervisor)
as a citation target. The full research findings live in engram under
the topic keys listed above. This stub exists to:

1. Resolve the broken cross-reference from ADR-001 to
   `docs/research/2026-08-feasibility.md`.
2. Document where the research actually lives (engram, until distilled).
3. Trigger a follow-up: distill the engram observations into this file
   during Phase 0 doc-surface flesh-out.

## Findings (short summary — full detail in engram)

**LLM-as-supervisor is architecturally viable** because:

- MCP (Model Context Protocol) gives read-only tool access with
  per-tool scoping — supervisor can inspect trades, positions, logs
  without touching order placement. See engram `hermes/research/track-8-mcp`.
- Dual-LLM approach (Claude reviews, GPT-4/Gemini audits) reduces
  single-model bias. See engram
  `decision: Layer G approved: LLM Supervisor Layer`.
- Phase 3+ activation only (never in Phase 0-2) ensures the deterministic
  engine is trusted first, LLM supervises second. Non-negotiable.

**LLM-generated trade signals are architecturally rejected** because:

- No reproducibility (temperature > 0 produces stochastic decisions)
- No audit trail (LLM cannot explain its own confidence)
- No backtestability (each backtest reprompts the LLM at API cost + noise)
- Regulatory posture: DGT + LOPDGDD require deterministic decision provenance for
  every trade in the FIFO ledger
- Full argument in engram
  `architecture: AI/ML research (Track 6) — the honest map beyond LLMs`

## What this closes

- ADR-001 status: Accepted
- ADR-005 status: Accepted (with 10-item ML unlock schedule by AUM)

## What this opens

- **Phase 0 follow-up**: distill this into a proper research digest
  (~1000-2000 words) once we have more `docs/research/` entries to
  compare style. Do not over-invest before the pattern stabilizes.
- **Phase 3+ follow-up**: MCP tool inventory — exhaustive list of
  read-only tools the supervisor gets, with per-tool auth scope.
  Deferred until Phase 3 approaches (~2028?).

## Related engram topic keys

- `hermes/research/track-1-vps` — deployment target research
- `hermes/research/track-2-exchange` — Binance vs Kraken vs others
- `hermes/research/track-3-tax` — DGT bindings + Modelo 100/721
- `hermes/research/track-4-observability` — structlog + healthchecks.io
- `hermes/research/track-5-testing` — pytest+hypothesis+mutmut
- `hermes/research/track-6-ai-beyond-llm` — ML applications map
- `hermes/research/track-7-pr-checks` — PR validation patterns
- `hermes/research/track-8-mcp` — MCP + LLM supervisor architecture
