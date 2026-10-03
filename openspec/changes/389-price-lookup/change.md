# Change: 389-price-lookup

**SDD change**: `389-price-lookup`
**Date**: 2026-10-03
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#389)

## Summary

Adds a read-only "price now" for the operator console: a `PriceLookupPort` and
a `BinanceTickerPriceAdapter` that reads `GET /api/v3/ticker/price` on the
allow-listed public market-data host. Quotes are cached for 5 seconds and
every request takes its weight from a token bucket. The strategy cannot
import it, so signals still come from closed bars only. The console wiring is
left to the console work, to avoid touching the files it is changing.

## Scope

### In

- `src/hermes/ports/price_lookup.py`: `PriceLookupPort`, `PriceQuote`.
- `src/hermes/adapters/market_data/binance_ticker.py`: the adapter.
- A recorded ticker fixture and unit tests, including a guard that no
  `hermes.domain.strategy` module or `hermes.ports.strategy` imports it.

### Out

- Using it in the console read model (#375, #403): the console writer wires
  it in.

## Decisions

### D-1: Ticker price endpoint on the existing market-data host

- **Decision**: `GET /api/v3/ticker/price?symbol=BTCUSDT` (weight 2) on
  `MarketDataConfig.base_url`, the same single-host allow-list the klines
  adapter uses. No key; a client with an API key header is refused.
- **Alternatives**: the latest 1m kline (heavier, and a bar, not a price);
  `/api/v3/ticker/24hr` (weight 2 per symbol, much larger body; today's
  change can come from the last closed 1d bar).
- **Cost of reversal**: LOW.

### D-2: 5-second cache, no retries

- **Decision**: reuse a quote for 5 s; on failure raise `NetworkError` or
  `ExchangeError` without retrying. The console shows the last value or a
  dash.
- **Rationale**: the issue asks for about 5 s; retries would only delay a
  display refresh.
- **Cost of reversal**: LOW.

### D-3: Test guard instead of an import-linter contract

- **Decision**: a unit test parses every strategy module and fails if one
  imports the port or the adapter.
- **Alternatives**: a new import-linter contract in `pyproject.toml`, which
  the console PR also edits. The test avoids a merge conflict and is just as
  strict.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Read-only, cached, budgeted price lookup

- **A-1.1**: `uv run pytest tests/unit/adapters/market_data/test_binance_ticker.py`

## Design

See the module docstring of `binance_ticker.py`.

## Test plan

1. A recorded ticker fixture, served by respx: price, symbol and `as_of`.
2. Five calls within 5 s send one request; the sixth second sends a second.
3. HTTP 400, non-JSON, wrong symbol, non-string and non-positive prices raise
   `ExchangeError`; a connect error raises `NetworkError`.
4. A client with an API key header is refused.
5. No strategy module imports the price lookup.
6. `bash scripts/dev/pr-preflight.sh` passes.

## Risks

- **Someone wires the price into a signal later**: the guard test fails, and
  ADR-011 parity review would catch it.
- **Binance changes the ticker body**: parsing fails loudly, and the console
  shows a dash.
