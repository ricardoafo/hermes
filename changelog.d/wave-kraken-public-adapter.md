### Added

- **Kraken public-endpoints adapter** (`src/hermes/adapters/exchanges/kraken_public.py`,
  `src/hermes/domain/fees/`, `src/hermes/domain/market_data/orderbook.py`,
  `src/hermes/domain/market_data/ticker.py`, `src/hermes/ports/fees.py`):
  read-only Kraken adapter covering `AssetPairs` (fee schedules), `OHLC`
  (klines), `Depth` (orderbook), and `Ticker` — no API key required, no
  signed methods. Enables cross-venue commission modelling for the
  backtest harness, adds Kraken as a second backtest source alongside
  Binance testnet, and gives us cross-venue price validation for free.
  Positions the adapter shape for MVP2 Kraken trading (signed methods
  slot in without reshaping the class — same HTTP client, rate limiter,
  and envelope parser). Domain additions: `FeeSchedule` / `FeeTier`
  value objects with volume-tier normalisation via `FeeSchedule.from_tiers`
  (schedules with arbitrary tier orderings always normalise ascending)
  and `Orderbook` / `Ticker` snapshots with crossed-book invariants.
  Wire failures wrap into new `KrakenPublicError` (`InfraError` subclass) —
  Kraken's `{"error": [...], "result": ...}` envelope, HTTP non-2xx,
  malformed JSON, missing pair, and unparseable Decimal all funnel
  through one exception type. Kraken's legacy X/Z asset-code prefixes
  (BTC/USD → XXBTZUSD, ETH/USDT → XETHZUSDT) are handled in the
  `_pair_to_kraken_symbol()` translator; unlisted pairs fall back to
  raw `BASEQUOTE` concatenation. Owned token-bucket rate limiter sized
  at Kraken's published 1 call/second public-tier cap. Fee schedules
  are cached in-memory with a 24 h TTL — schedules change
  weeks-to-months apart and the commission use case reads on every
  price event, so caching drops the request rate ~99% while still
  catching schedule changes within a business day. Market data
  (klines, ticker, orderbook) is never cached — always fresh from the
  wire. Contract tests via the `contract` marker hit real Kraken (no
  auth needed) for AssetPairs / OHLC / Ticker and detect upstream
  drift before unit mocks lie about production. `config/kraken_public.yaml`
  + `HERMES_KRAKEN_PUBLIC__<FIELD>` env overrides wire the adapter into
  `HermesConfig`. Zero new runtime dependencies. Related: MVP1 status
  (#141); shadow-mode integration follow-up (#175); operator
  conversation 2026-08-24 (commission calc need + EU regulatory hedge).
