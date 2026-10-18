# Change: 148-binance-testnet-spot

**SDD change**: `148-binance-testnet-spot`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#148)

## Summary

Adds the Binance spot trading adapter behind the existing `ExchangePort`. It
places market orders, places immediate-or-cancel limit orders, cancels
orders, reads the order book, balances and commission rates, and rests an
exchange-side stop-loss through a new `StopLossPort`. Requests are signed
with HMAC-SHA256 over the exact query string. The adapter only talks to
testnet unless the composition root passes `allow_mainnet`, which it does only
after the live-approval gate (#382, ADR-014). Nothing wires it into
`hermes serve`, which stays shadow.

## Scope

### In

- `adapters/exchanges/binance_spot.py`: `BinanceSpotAdapter`,
  `BinanceSpotConfig`, `sign`.
- `ports/exchange.py`: `StopLossPort`.
- Unit tests with respx replies shaped like the Binance spot REST
  documentation, and a credential-gated testnet contract test.

### Out

- Wiring the adapter into a live path or the order router: after
  certification (ADR-014).
- A `hermes health binance` probe: follow-up.

## Decisions

### D-1: Raw httpx with our own signing, sync like the klines adapter

- **Decision**: no exchange SDK; HMAC-SHA256 per Binance's signing rules,
  pinned by the documentation's published example.
- **Rationale**: the issue's design constraint, and the existing adapter
  pattern.
- **Cost of reversal**: LOW.

### D-2: Testnet by construction

- **Decision**: `BinanceSpotConfig` refuses any host other than
  `testnet.binance.vision` unless `allow_mainnet=True`. Credentials come from
  `HERMES_BINANCE__API_KEY` and `HERMES_BINANCE__API_SECRET`, and the secret
  is kept out of `repr`.
- **Rationale**: AGENTS.md rule 7, ADR-003 §00 I-08, ADR-014.
- **Cost of reversal**: LOW.

### D-3: Limit orders are IOC; the stop-loss is a GTC stop-loss-limit

- **Decision**: `place_limit_order` uses IOC, so a miss is a `Rejection`, as
  the port documents. `place_stop_loss` rests a SELL `STOP_LOSS_LIMIT`
  triggered at the stop, with its limit 1% below (`stop_limit_buffer`). It
  is a safety net for an outage, not a strategy rule; the strategy's own
  exits still run.
- **Cost of reversal**: LOW.

### D-4: Fees in the quote currency

- **Decision**: commissions charged in the quote are summed, commissions in
  the base are priced at the fill price, and a third asset (such as BNB)
  raises, because pricing it needs another market.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Signed, testnet-only trading behind the port

- **A-1.1**: `uv run pytest tests/unit/adapters/exchanges/test_binance_spot.py`

## Design

See the module docstring of `binance_spot.py`.

## Test plan

1. The signature matches Binance's documented example.
2. A mainnet host is refused without permission; the secret is not in `repr`.
3. Each order type, the cancel path (including the idempotent -2011), the
   order book, balances, fees, rate-limit replies and transport errors.
4. The contract test round-trips a resting stop on the real testnet when
   credentials are set.
5. `bash scripts/dev/pr-preflight.sh` passes.

## Risks

- **Reply shapes differ from the documentation**: the contract test catches
  that on testnet before any live use.
- **Wrong host in a config**: refused at construction unless `allow_mainnet`.
