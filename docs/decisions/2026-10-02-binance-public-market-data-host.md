# Binance public market-data host for the live closed-bar source

**Date**: 2026-10-02
**Author**: AI Bot on behalf of the Code Owner
**Related**: [2026-08-23 Binance testnet klines](2026-08-23-binance-testnet-klines.md)
(its "What would change this decision" section asks for this entry),
ADR-011 (paper-trading engine, shadow mode), AGENTS.md prohibition 7, GitHub
issues #307 and #301, `openspec/changes/307-live-bar-stream/change.md` (D-1, D-2)

## Decision

The live closed-bar source reads Binance public market data from
`https://data-api.binance.vision`, polling `GET /api/v3/klines` and
`GET /api/v3/time` with no credentials, and it is not tied to `allow_mainnet`.

## Context

Shadow mode (ADR-011) evaluates the swing strategy on live closed bars. The
2026-08-23 entry chose the testnet for historical klines and named the
condition for moving to mainnet public reads: testnet data diverging from the
real market, plus a follow-up entry naming the exact endpoint and limits.

The divergence exists. On 2026-10-02 the BTCUSDT 4h bar opening at 12:00 UTC
had a low of 85162.90 and volume 6577.59 on the real market, against 75000.00
and 213.05 on the testnet. A range of 12,745.57 against 2,057.10 distorts ATR,
Donchian and Chandelier values, so testnet bars cannot feed shadow evidence.

AGENTS.md prohibition 7 bars real-capital API calls from tests and dry-run
flows. `data-api.binance.vision` is documented by Binance as a public
market-data host, and it answers `Access-Control-Allow-Methods: GET, HEAD,
OPTIONS`. Trading endpoints live on `api.binance.com`, which stays out of reach.
Three layers enforce the boundary:

1. `MarketDataConfig` accepts only this host as `base_url`.
2. The adapter sends only GET to `/api/v3/klines` and `/api/v3/time`, and has
   no key or signature code.
3. Tests fail on any other verb, path, host or credential header.

Alternatives considered:

- **`api.binance.com`**: hosts the trading endpoints. Rejected.
- **Testnet**: fails on fidelity, shown above. Rejected.
- **Public WebSocket**: not rejected here; the polling choice and its
  rationale are in change spec decision D-1.

## Consequences

- This entry supersedes the testnet-only stance of the 2026-08-23 entry for
  live bars only. The historical-klines fetcher stays on the testnet.
- `allow_mainnet` governs trading adapters and is untouched. The source works
  with `allow_mainnet` false in production.
- A 451 or 403 from the VPS region is permanent and fails loudly at warm-up.
  Before the first deploy the operator confirms `GET /api/v3/time` returns 200
  from the VPS.
- Order book diffs stay on `BinanceLiveWsAdapter`, whose default hosts are the
  full trading hosts. Moving them is a follow-up.
- Revisit if Binance widens the host beyond market data, or if its bars stop
  matching the Vision archives.
