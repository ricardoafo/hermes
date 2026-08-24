# Kraken public-endpoints adapter — read-only for MVP1

**Date**: 2026-08-24
**Author**: Code Owner (via ai-bot)
**Related**: ADR-004 (Binance primary, Kraken hot backup), MVP1 status
(#141), operator conversation 2026-08-24 (Kraken commission calc need +
EU regulatory hedge), Meta shadow-mode integration (#175 — future).

## Decision

Ship a `KrakenPublicAdapter` under `src/hermes/adapters/exchanges/`
that queries Kraken's public REST endpoints — fee schedules
(`AssetPairs`), historical klines (`OHLC`), orderbook depth (`Depth`),
and ticker (`Ticker`). **No auth. No API keys. No signed trading
methods.** The adapter shape mirrors `BinanceKlinesAdapter` +
`FrankfurterAdapter` (raw `httpx.Client`, owned token-bucket limiter,
Decimal-at-the-port-boundary) so the private-trading methods slot in
without reshaping the class when MVP2 needs them.

## Context

Three concrete needs land on this decision:

1. **Commission calculations** — the cross-venue backtest harness needs
   Kraken's volume-tiered fee schedule to model executions on the
   Kraken side. Without it, backtests either hard-code a stale fee
   number (drift risk) or refuse to price a Kraken leg (feature gap).
2. **Backtest source diversity** — Binance testnet is our primary
   klines source (ADR-004 §04). Adding Kraken as a second source gives
   us cross-venue price validation for free and hedges against Binance
   endpoint outages during a backtest run.
3. **EU regulatory hedge** — the operator conversation on 2026-08-24
   flagged the tail risk of MiCA + national restrictions squeezing
   Binance's EU accessibility. Kraken is US-headquartered, has EU
   entities (Ireland), and has historically been more compliant with
   EU rules. Positioning our adapter fleet so a Kraken trading adapter
   is a small delta (add signed methods to this class) — instead of
   greenfield — is cheap insurance.

## Alternatives considered

- **Wait for MVP2 and ship a signed Kraken adapter directly**. Rejected:
  MVP1 needs the commission calc + backtest source RIGHT NOW; deferring
  blocks two live features on a MVP2-scoped auth surface (KYC'd Kraken
  account, key management, nonce discipline, signed-request tests).
  The public endpoints deliver everything MVP1 needs and unlock the
  signed path as a small future PR (add methods, add auth headers,
  wire the private test suite).
- **Only ship the fee-schedule endpoint** (drop klines/orderbook/ticker
  from this PR). Rejected: the four endpoints share one HTTP client,
  one rate limiter, one error type, one cache layer, and one pair-symbol
  translator — splitting them is a net negative for code volume, and
  the marginal cost of shipping three more methods on the same wire
  surface is under 200 LOC.
- **Use `krakenex` or `python-kraken-sdk`**. Rejected: same reasoning
  as ADR-003 §04 for Binance — a raw `httpx` adapter is under 700 LOC,
  matches the anyio-on-asyncio policy exactly, has zero dependency
  supply-chain surface, and lets us shape errors and rate-limiting
  the way the rest of the fleet does (no impedance mismatch between
  three exchanges each with their own SDK's exception hierarchy).

## Rationale

- **Public-only** is the right MVP1 scope: it delivers the two live
  use cases (commissions + backtest source), positions the shape for
  MVP2 trading (already-owned rate limiter + Kraken envelope parser),
  and requires zero secrets management. Reviewers can approve the PR
  without any auth/compliance review.
- **Fee-schedule caching (24 h TTL)** — Kraken's schedules change
  weeks-to-months apart. The commission-calc use case reads the schedule
  on every price event; without a cache, that's ~360_000 calls / day
  for one strategy — well above Kraken's 1-req/s public tier. A 24 h
  cache brings it to ~1 call / day / pair while still catching
  fee-schedule changes within a business day (Kraken emails ~1 week
  notice before schedule changes, so 24 h lag is inside the notification
  window).
- **Never cache market data** — klines, orderbook, ticker always hit
  the wire. The backtest use case is deterministic replay from
  fetched-once data (no re-request needed); the price-validation use
  case needs freshness by construction.
- **Rate limiter sized to 1 call/second** — Kraken's published
  public-tier cap. Anyone raising this without also adding the signed
  endpoints (which grant a higher tier) is going to eat 429s.
- **Kraken pair naming quirk (X/Z prefixes)** — the adapter's
  `_pair_to_kraken_symbol()` translates the well-known pairs
  (BTC/USD → XXBTZUSD, ETH/USDT → XETHZUSDT) via a hard-coded override
  table and falls back to raw `BASEQUOTE` concatenation for unlisted
  pairs. This is intentionally simple; every override is unit-tested,
  and unknown pairs fail fast with Kraken's own "Unknown asset pair"
  error message instead of a mystery symbol.
- **Error envelope** — Kraken wraps every response as
  `{"error": [...], "result": {...}}`. Non-empty `error` is the
  primary business-level failure surface; the adapter raises
  `KrakenPublicError` (an `InfraError` subclass) with the raw Kraken
  strings preserved for triage.

## Consequences

- New adapter at `src/hermes/adapters/exchanges/kraken_public.py`; new
  port at `src/hermes/ports/fees.py`; new domain module
  `src/hermes/domain/fees/` (FeeSchedule + FeeTier value objects); new
  domain types under `src/hermes/domain/market_data/`
  (`Orderbook` + `Ticker`).
- New error subclass `KrakenPublicError(InfraError)` — catchable via
  `except InfraError` for the "any adapter failure" bulk path.
- New config `KrakenPublicConfig` (pydantic) + `config/kraken_public.yaml`
  stub. Env-override prefix `HERMES_KRAKEN_PUBLIC__<FIELD>`.
- Contract tests (`tests/contract/test_kraken_public_*.py`) hit real
  Kraken — no env-var gate (no auth needed). Rate-limited to a single
  call each, all skip-safe via the `contract` marker (per-PR unit runs
  never touch the wire).
- MVP2 promotion path: add `sign_request()` helper, add
  `place_order()`, `cancel_order()`, `get_open_orders()`, etc. — all
  as new methods on the same class. The public methods stay untouched;
  the signed methods reuse the shared HTTP client, rate limiter, and
  envelope parser. Estimated delta: ~300 LOC + a signed-endpoint test
  suite behind an env-var gate (`HERMES_KRAKEN_PRIVATE__API_KEY`).

## What would change this decision

- If Kraken's public-tier rate cap tightens below 1 req/s (unlikely —
  their published limit has been stable for years), the fee-schedule
  cache TTL becomes load-bearing. Consider promoting the cache to a
  cross-process shared cache (SQLite row per pair) so parallel
  processes share the same schedule.
- If MiCA + national restrictions materially reduce Binance EU access
  ahead of MVP2, this adapter's signed-endpoint promotion moves onto
  the MVP1 critical path and the "MVP2 scope" framing above needs a
  revision entry.
- If Kraken changes their fee-schedule response shape (e.g. drops
  `fees_maker` in favour of a merged tier array), the contract test
  `tests/contract/test_kraken_public_fees.py` catches the drift
  before the unit mocks lie about production reality — the fix is a
  targeted `_parse_fees_response()` update, not a wholesale rewrite.
