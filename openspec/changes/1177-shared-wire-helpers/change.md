# Change: 1177-shared-wire-helpers

**SDD change**: `1177-shared-wire-helpers`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1177-shared-wire-helpers/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The HTTP adapters each carried their own copy of the same wire helpers:
seven decimal parsers, seven Binance symbol builders, two fee-percent
converters, two window checks, four https/timeout config checks and two
token-bucket limiters with two `RateLimitConfig` types. The copies had
drifted: two parsers read a JSON `true` as a number until `Decimal` failed
on the text `"True"`. One package, `hermes.adapters._shared`, now holds one
copy of each; every adapter calls it with its own error type and message
prefix. The main risk is changing an error message a caller or test
matches on; the cross-adapter tests pin the message shapes.

## Scope

### In

- `adapters/_shared/wire.py`: `wire_decimal`, `binance_symbol`,
  `percent_to_bps`, `validate_utc_window`, `validate_http_config`.
- `adapters/_shared/rate_limiter.py`: the exchanges limiter, moved; the fx
  copy and its test deleted.
- Every adapter and entrypoint that used a copy now imports the shared one.
- A source-scan guard and property tests (see Test plan).

### Out

- Ledger row codecs and reader lifecycle (issue #1178).
- The repo-wide duplicate-block ratchet (issue #1201).
- Kraken's pair-name fallback, which concatenates codes but is not a
  Binance symbol.

## Decisions

### D-1: The caller passes its own error builder

- **Decision**: `wire_decimal(raw, *, field, error)` takes a
  `Callable[[str], Exception]`; adapters pass `ExchangeError`,
  `KrakenPublicError` or `FxRateError`.
- **Alternatives**: one shared `WireError` type (would bypass each
  caller's retry and degraded-feed handling, which catches its own type).
- **Rationale**: keeps every `except` in the application layer unchanged.
- **Cost of reversal**: LOW.

### D-2: Strings-only is a flag, not a second parser

- **Decision**: `numbers=False` for endpoints that always quote numbers
  (depth snapshot, ticker, Vision CSV), refusing a JSON number as
  "not a string".
- **Alternatives**: accept numbers everywhere (silently widens three
  parsers).
- **Rationale**: no behaviour change for those endpoints.
- **Cost of reversal**: LOW.

### D-3: A negative fee percent is refused on Kraken spot too

- **Decision**: `percent_to_bps` refuses a negative percent for both
  Kraken adapters; only the public one did before.
- **Alternatives**: a flag to allow negatives (no Kraken spot fee is
  negative, so nothing would use it).
- **Rationale**: one rule for one quantity.
- **Cost of reversal**: LOW.

## Requirements

### R-1: One copy

No module under `adapters/exchanges`, `market_data`, `fx` or
`healthchecks` catches `InvalidOperation`, calls `is_finite()`, defines a
`RateLimiter`/`RateLimitConfig` or builds a Binance symbol by hand.

### R-2: Bool, NaN and infinity are refused with the caller's error

For every adapter parser and every caller error type, a JSON bool, NaN or
infinity raises that caller's error naming the value.

### R-3: Finite values parse exactly as before

A numeric string parses to `Decimal(raw)`, a JSON float to
`Decimal(str(raw))`, an integer to itself.

## Design

`wire_decimal` checks the type (refusing `bool` first), parses with
`Decimal(str(raw))`, then checks `is_finite`, raising
`error(f"{field} is not numeric|a string|a decimal|a finite number: {raw!r}")`.
Each adapter keeps a one-line private binding (for example
`_decimal_or_raise`) that supplies its error and prefix, so call sites
are unchanged. `validate_http_config(owner, *, url, timeout_sec,
url_field)` produces the same messages the configs raised before.

## Test plan

- `tests/unit/adapters/_shared/test_wire.py`: hypothesis tests for R-2 and
  R-3 over all three error types, unit tests for the other helpers, and
  the R-1 source scan.
- `tests/unit/adapters/test_wire_numbers.py`: every adapter parser refuses
  a JSON bool with its own error (added beside the existing NaN case).
- `tests/unit/adapters/_shared/test_rate_limiter.py`: the moved limiter
  suite.

## Risks

- Two message wordings changed ("not parseable as Decimal" and "not
  string or numeric"); the two tests that matched them are updated in the
  same commit.
