# ADR-013: Shadow composition root lives in `entrypoints/serve.py`; Shadow market data is the REST klines source

**Date**: 2026-10-03
**Status**: Accepted — under the Code Owner's pre-approval of change `309-serve-bar-loop` (#309), 2026-10-03
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: ADR-011 D-02 (location of the composition root) and the Shadow row of the ADR-011 mode table, see [ADR-011](adr-011-paper-trading-engine.md). Nothing else in ADR-011 changes.

---

## Context

ADR-011 D-02 says mode selection happens at a composition root and that the
root is `compose_engine()` in the application layer. Two facts since then
make that text wrong for the Phase-1 shadow loop:

1. The import-linter contract in `pyproject.toml` forbids the application
   layer from importing adapters. Choosing a concrete adapter is that
   import, so a root in `application/` cannot exist without weakening the
   rule that protects the domain. The root has lived in
   `src/hermes/entrypoints/serve.py` since #305.
2. The mode table names `LiveWebSocketMarketDataAdapter` for Shadow. Change
   `307-live-bar-stream` (D-1) chose public REST klines polling instead
   (`BinanceLiveKlinesAdapter`): the strategy decides once per 4h bar and
   fills at the next bar open (ADR-011 D-10), so WebSocket latency buys
   nothing.

ADR-011 became Accepted on 2026-10-03 and is immutable (ADR-009), so the
correction is a new ADR, not an edit.

## Decision

### 1. Composition root (supersedes ADR-011 D-02)

The composition root is `src/hermes/entrypoints/serve.py`, not the
application layer. The mode is chosen by which adapters `serve` wires.
Phase 1 wires market data, strategy, ledger and notifiers, and wires no
exchange adapter, so no order can be placed by construction. The real-money
gate stays on `HermesConfig` (`env` and `allow_mainnet`). `HERMES_SHADOW` is
not read: #175 was closed as not planned and the port swap makes it
redundant.

### 2. Shadow market data (supersedes the Shadow row of the ADR-011 mode table)

| Mode | `MarketDataPort` |
| ---- | ---------------- |
| **Shadow** | `BinanceLiveKlinesAdapter` (public REST klines, polled after each 4h and 1d close; change `307-live-bar-stream`, D-1) |

The WebSocket adapter stays for order book diffs. The Live (MVP2) row of the
ADR-011 table is unchanged.

## Alternatives considered

- **`compose_engine()` in `application/` as D-02 says**: needs application to
  import adapters, which the contract forbids.
- **Relax the import-linter contract**: weakens the rule that keeps the
  domain pure.
- **Edit ADR-011**: forbidden for an Accepted ADR (AGENTS.md rule 10).

## Consequences

- ADR-011 D-02 and its Shadow mode-table row are superseded by this record;
  read them together with it. Every other ADR-011 decision stands.
- ADR-011 still says the pre-registration is pending signature in three
  places. That is stale: `docs/experiments/swing-mvp1/pre-registration-v1.md`
  was signed on 2026-08-25. The mentions are left as-is, as the ADR is
  immutable; this record is the correction.
- ADR-012 is reserved by a separate draft; this ADR takes the next free
  number.
- Cost of reversal: LOW. Moving the root only moves wiring code.

## References

- `openspec/archive/309-serve-bar-loop/change.md` D-1
- `openspec/archive/307-live-bar-stream/change.md` D-1
- [ADR-011](adr-011-paper-trading-engine.md) D-02, D-10
- [ADR-009](adr-009-documentation-strategy.md) (ADR immutability)
