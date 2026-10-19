# Retire the simulated exchange adapter

**Date**: 2026-10-18
**Author**: AI Bot on behalf of the Code Owner
**Related**: [ADR-011](../architecture/adr-011-paper-trading-engine.md),
[ADR-013](../architecture/adr-013-shadow-composition-root.md),
GitHub issues #1181, #353, #238

## Decision

`SimulatedExchangeAdapter` (`src/hermes/adapters/exchanges/simulated.py`) is
deleted. This supersedes the `ExchangePort` column of the Backtest and Shadow
rows of the ADR-011 mode table, which name it. Decided by the Code Owner on
18 Oct 2026 (issue #1181).

## Context

Nothing in production built the adapter: its only importer was the package
re-export. The fill path it was meant to carry runs elsewhere:

- **Backtest**: `FillKernel` with `OrderbookWalkFillModel`
  (`scripts/backtest/run_calibration_full.py`).
- **Shadow**: `ShadowExecutor` around the same `FillKernel`, fed by
  `BinanceDepthSnapshotAdapter` (`hermes serve`), per #353.

ADR-013 already superseded the Shadow row's market data and the location of
the composition root. Keeping the adapter meant a second balance and lot
ledger beside the portfolio tracker that no mode used. ADR-011 stays as
written, since accepted ADRs are immutable.

## Consequences

- Backtest and Shadow have no `ExchangePort`. Fills come from the
  `FillKernel`, and the tracker and ledger lots are the only books.
- The Live row (MVP2, real exchange adapter) and the single fill kernel
  rule (D-01) are unchanged.
- The adapter's own tests go with it. The EUR shadow-safety test now drives
  `hermes serve` itself.
