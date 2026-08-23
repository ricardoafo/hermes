# Binance testnet as historical-klines source; SQLite for kline storage

**Date**: 2026-08-23
**Author**: Code Owner (via rafo-claude-bot)
**Related**: ADR-003 §04 (exchange adapters), ADR-003 §03 (persistence schema —
`prices` table), ADR-004 (Binance primary via ARG-DNI, Kraken hot backup),
GitHub issue #28

## Decision

The historical-klines fetcher hits **Binance testnet** at
`https://testnet.binance.vision/api/v3/klines` for public OHLCV data, and
persists the results to **SQLite** — the same backend as the rest of hermes
state, per ADR-003 §03 (`prices` table already sketched in the schema).

## Context

Issue #28 (Phase 1 — Simulation) needs a rate-limited, resumable fetcher
that feeds the backtest harness. Two questions had to be settled before code:

1. **Which Binance endpoint?** Testnet (`testnet.binance.vision`) vs mainnet
   (`api.binance.com`). Testnet klines are public (no API key) and free.
   Mainnet klines are also public — but AGENTS.md prohibition #7 blocks any
   real-capital API path in test/dev code, and the pre-Nov-2026 tree is dev
   code by definition. Testnet also matches the existing
   `config/exchanges/binance.yaml` which points at `testnet.binance.vision`
   by default.
2. **Which storage?** Parquet vs SQLite. Parquet is a natural fit for
   read-heavy OHLCV replay: columnar, compressed, portable, pyarrow-fast.
   SQLite is what the rest of hermes uses (ADR-003 §03) and the persistence
   schema already sketches a `prices` table with the exact columns a kline
   store needs (open/high/low/close, EUR-priced, candle time).

## Alternatives considered

**Endpoint**:

- Mainnet public klines — rejected: any mainnet code path breaks the "no
  real-capital APIs in dev" invariant (AGENTS.md #7). Cost is zero; benefit
  is nil (testnet returns the same OHLCV shape).
- Third-party historical archives (Kaggle dumps, Coinbase snapshots) —
  rejected: adds a supply-chain surface and drift between our replay data
  and what Binance actually reports.

**Storage**:

- Parquet — technically superior for pure read-heavy replay, but forces a
  second storage runtime (pyarrow) into hermes for one feature. Every other
  bit of hermes state (trades, tax lots, heartbeats, events, prices) lives
  in SQLite. Introducing parquet here means every reader has to know two
  formats; the backup story branches; the on-disk footprint scatters.
  Deferred: if replay performance becomes a bottleneck at Phase 2 scale, a
  parquet snapshot path can be added on top of the SQLite source of truth
  without changing the fetcher interface.
- In-memory only — rejected: loses the "resumable" property; every restart
  re-fetches from Binance and re-eats rate budget.

## Rationale

- **Testnet**: zero risk of a wrong-URL bug pointing dev code at real
  capital paths. Public data means no auth surface. Same wire format as
  mainnet — a future promotion to mainnet reads is a config-URL flip, not
  a code change.
- **SQLite**: one storage backend across hermes = one backup story, one
  concurrency model (WAL, single writer), one debug surface. The `prices`
  table sketched in ADR-003 §03 is exactly the shape a kline store needs;
  we materialise it in the first migration.
- **Rate-limit source**: Binance publishes weight limits at
  <https://binance-docs.github.io/apidocs/spot/en/#limits>. Klines endpoint
  is weight 1 per request; the global cap is 6000 request-weight per minute
  for the public IP-limited pool. This adapter buffers well below that
  (default 5000 weight/minute, matching the number already used in the
  `BinanceAdapter` example in ADR-003 §04).

## Consequences

- The fetcher lives at `src/hermes/adapters/exchanges/binance_klines.py`
  with a private rate limiter at `.../rate_limiter.py`.
- The kline store lives at `src/hermes/adapters/market_data/kline_store.py`
  and owns its own SQLite migration (`0001_kline_store.sql`) — separate
  from the future `ledger` migrations because the two adapters have
  independent lifecycles.
- Decimal columns are stored as `TEXT` (matches the ledger convention
  ADR-003 §03 cites — SQLite has no native decimal type; TEXT round-trips
  losslessly via `Decimal(str)`).
- Follow-up work is deferred (per issue #28's "spawn sub-issues" mandate):
  resumability + integrity checksums, multi-exchange fetcher factory,
  backfill orchestrator. Each becomes its own issue after this PR merges.

## What would change this decision

- If Phase 2 backtest workloads make SQLite read latency a bottleneck at
  scale, add a parquet materialised view alongside SQLite (SQLite stays
  source-of-truth for consistency; parquet becomes an idempotent
  read-optimised replica).
- If Binance testnet klines diverge from mainnet in coverage or accuracy
  (unlikely but not impossible), migrate to mainnet public reads with an
  explicit "read-only, IP-limited pool" tag in the code and a follow-up
  decision entry documenting the exact endpoint + limits.
