# Change: 971-order-endpoints

**SDD change**: `971-order-endpoints`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/971-order-endpoints/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Part 3 of issue #971: "Binance spot order endpoints (new order, query,
cancel, open orders, account) in the production wire format, with fills
from the existing fill kernel, so the real order adapters run with no real
key." The fake market now answers the signed endpoints
`BinanceSpotAdapter` calls on `testnet.binance.vision`, in process and over
the loopback service. Issue #1080 (exchange-held stops) is tested against
these endpoints, so resting stop-loss sells, cancels and lost answers are
in scope.

## Scope

### In

- `tests/support/fake_market/spot.py`: `SpotAccount`, the matching rules,
  the request checks and the `Lost` faults.
- `FakeExchange` routes the testnet host: public endpoints as before, the
  signed ones to `FakeExchange.spot`.
- The service serves `/binance-spot/...` as the testnet host, forwards the
  API key header and the query byte for byte, answers `DELETE`, and takes
  an `order_lost` fault.
- `tests/integration/fake_market/test_fake_market_orders.py`.

### Out

- Partial fills, maker fills, exchange filters (lot size, tick size,
  notional) and order types the adapter does not send.
- Packaging the service in the release bundle and the installed-wheel
  `hermes serve` run: later parts of issue #971.

## Decisions

### D-1: Fill through `OrderbookWalkFillModel`

- **Decision**: market and IOC orders fill through the kernel the backtests
  use, against the depth book the fake serves at that instant.
- **Alternatives**: a separate price walk in the fake.
- **Rationale**: one fill rule for backtests and the fake exchange; the
  issue asks for it.
- **Cost of reversal**: LOW.
- **Consequence**: fills are all or nothing, so an IOC the book cannot fill
  whole ends `EXPIRED` with nothing executed, where Binance could fill part.

### D-2: Stops trigger on closed 4h bars

- **Decision**: a resting stop-loss sell triggers on the first closed 4h
  bar opened after it was placed whose low reaches the stop, and fills at
  the stop, at a gapped open above its limit, or at its limit once a later
  bar's high reaches it. Stops settle when the next signed request arrives.
- **Alternatives**: trigger on the forming bar, or on the depth mid.
- **Rationale**: the fake has no trades between bars; closed bars are what
  a scenario scripts, and the rule covers the gap cases #1080 must handle.
- **Cost of reversal**: LOW.

### D-3: Check requests as Binance does

- **Decision**: the API key header, the HMAC signature over the exact
  query and the timestamp against the scripted clock and `recvWindow` are
  checked, with Binance's error codes.
- **Rationale**: a signing or clock bug in the adapter shows up in QA
  rather than on the first real request.
- **Cost of reversal**: LOW.

### D-4: Both Binance hosts are one upstream for faults

- **Decision**: `/binance-spot` shares the `binance` upstream's
  `rate_limited` and `slow` faults.
- **Rationale**: Binance limits by account and IP, not by host; the
  existing control API stays as it is.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Fills from the served book

A market or IOC order fills at the kernel's walk of the served book, and
pays the 0.1% taker rate in the base asset on a buy and the quote asset on
a sell; balances move by exactly that.

### R-2: Stops hold, cancel and fill

A stop-loss sell locks its quantity, is listed open, frees it on cancel
(a second cancel is -2011), and fills per D-2.

### R-3: Refusals move nothing

A stop at or above the last price, a short balance, a bad key (-2015), a
bad signature (-1022) and a stale timestamp (-1021) are refused and leave
the account unchanged.

### R-4: Lost answers

`Lost.PLACED` answers 503 after placing, and the adapter's lookup recovers
the same fill or stop; `Lost.DROPPED` answers 503 without placing, and the
adapter reports the order not placed.

### R-5: The service is transparent

Over loopback the signature, the key header and `DELETE` reach the account
unchanged, and `order_lost` is reachable from the control API.

## Design

`SpotAccount.handle` authenticates, settles resting stops against the bars
closed since each was placed, then routes by method and path. Orders keep
Binance's shapes (`ACK`, `FULL`, the query shape, `myTrades` rows, the
account fixture's shape). `FakeExchange` exposes `now`, `pair_for`,
`last_close` and `closed_h4` to the account through the `Venue` protocol.

## Test plan

`tests/integration/fake_market/test_fake_market_orders.py` drives the real
`BinanceSpotAdapter` on its testnet config with the fake as its transport,
and over the loopback service, for R-1 to R-5. Each invariant was broken
in the fake (taker rate, gap rule, the stop's lock, the signature check,
the lost fault, the immediate-trigger check) and the tests failed.

## Risks

- All-or-nothing fills differ from Binance for IOC orders larger than the
  book at the limit (D-1). The adapter treats both as a rejection only when
  nothing executed, so a scenario that needs a partial IOC needs a later
  change.
