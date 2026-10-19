# Issue: spec-1-domain-aggregates

**SDD change**: `spec-1-domain-aggregates`
**Status**: In Progress
**Date**: 2026-08-25

## Tracking Summary

Adds two foundational domain changes that unblock the paper-trading engine (Specs 2-9): (1) a
`StrategyId` value object added as a mandatory field on `Lot`, making every lot bucket-aware from
day 1 per SA-6's finding that strategy attribution must be first-class — not retrofitted; (2)
`event_seq: int` and `payload_sha256: str` (BLAKE2b-256, auto-computed at construction) on
`DomainEvent`, enabling the event-sourcing hash chain required for deterministic replay per SA-8.
The change is additive on domain code and a backward-compat break only on tests (all event test
fixtures must supply `event_seq`). This is the G1 gate item per the `success-criteria.md` roadmap.
