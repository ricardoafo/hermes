- **Binance Vision aggTrades support + CoinGecko peg downloader + 404
  classification fix** (`scripts/data/ingest_binance_vision.py`,
  `scripts/data/ingest_coingecko_peg.py` NEW,
  `openspec/changes/data-ingest-agg-trades-and-peg/`): extends the
  klines-only ingest from #218 (PR #219) to cover the remaining MVP1
  historical data classes. (1) `--include` flag on the Binance script
  accepts `klines` (default), `agg_trades`, or `all`. aggTrades feeds
  intrabar slippage calibration (Spec 6) and fill-timing modelling
  (Spec 2b). Same SHA256 verify, same manifest, same resume logic;
  manifest keys are now namespaced (`klines/BTCUSDT/1h/2020-09`,
  `agg_trades/BTCUSDT/-/2020-09`) with backward-compat migration for
  the legacy 3-part format. (2) 404 responses for future months are
  now classified as `not_yet_published` (informational) rather than
  `failed`, so the script exits 0 when everything worked as expected —
  fixes the mis-report observed when running before end-of-window
  months were published. (3) New `ingest_coingecko_peg.py` downloads
  hourly USDT / USDC / DAI prices vs USD in month windows for the
  SA-9 depeg overlay (pre-reg v1 §5.5 correlation lock + depeg
  WARN 1.5% / HALT 3% rules) that Spec 8 will wire. Free-tier rate
  limit respected via 4-second inter-request sleep (~15 req/min,
  well under the 30/min ceiling). Both scripts remain stdlib-only.
