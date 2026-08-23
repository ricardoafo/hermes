# FX rate source — Frankfurter (ECB reference-rate wrapper) for EUR conversion

**Date**: 2026-08-23
**Author**: Code Owner (via ai-bot)
**Related**: ADR-002 §Decision 4 (EUR-first accounting, ECB reference rates
mandatory for tax reporting), MVP1 status meta-issue #141

## Decision

The FX rate provider port
(`src/hermes/ports/fx.py::FxRateProviderPort`) is implemented by
`FrankfurterAdapter` (`src/hermes/adapters/fx/frankfurter.py`), which
hits the public Frankfurter API at `https://api.frankfurter.app`.
Frankfurter is a free, keyless wrapper over ECB reference rates. It
returns clean JSON with an ISO 8601 date and a `rates` mapping. Rates
arrive as JSON numbers; the adapter converts them to `Decimal` via
`Decimal(str(value))` so IEEE-754 float error never leaks into the
domain.

Rate limiting is a polite 60 req/min bucket (Frankfurter publishes no
cap; polite defaults are the right call for a free hobbyist API).
Errors are wrapped in `FxRateError` (an `InfraError` subclass) so
callers see one exception type across HTTP failures, unparseable
responses, and missing target currencies.

## Context

MVP1's tax pipeline needs an authoritative EUR conversion path. Two
questions had to be settled:

1. **Which rate source?** Exchange rates (Binance/Kraken tickers) vs
   ECB reference rates.
2. **How to reach ECB?** Direct ECB XML endpoint vs a wrapper API.

Spain's LOPDGDD- and IVA-adjacent tax filings expect ECB reference
rates for cross-currency conversion. Exchange spot prices are display-
only per ADR-002 (`spot_price_source` documented as informational,
never as the authoritative EUR value stored in the ledger).

## Alternatives considered

### Source

- **Exchange tickers (Binance/Kraken)** — REJECTED. Not authoritative
  for AEAT: the tax filing must be reproducible from a published
  official reference rate, not from a venue-specific ticker that
  varies by microsecond and by which market maker was on top of the
  book. Also creates circular failure: an outage on the same venue
  we're trading through disables tax reporting for the day.
- **Third-party historical archives** — REJECTED. Coverage gaps
  (weekends, holidays, market closures) and no clear provenance chain
  back to the ECB, which is what AEAT actually cares about.

### Wrapper vs direct ECB

- **ECB direct (XML feed)** — VIABLE FALLBACK. Same underlying data.
  API is XML-only (`https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml`),
  no query params for historical dates (must fetch the whole daily
  file), and the historical corpus is a separate ZIP download. Roughly
  ~30 LOC change to swap our adapter to it if Frankfurter goes away
  (parse XML instead of JSON, cache the daily file, filter to the
  requested pair). Not chosen today because Frankfurter's JSON is
  cleaner and its `/YYYY-MM-DD?from=X&to=Y` shape maps directly onto
  our port.
- **Frankfurter (wrapper)** — CHOSEN. Clean REST JSON API, keyless,
  built-in support for historical dates and specific currency pair
  filtering, business-day fallback (asks for Sunday → returns prior
  Friday's rate). Downside: single-maintainer hobbyist project; can
  go down. The fallback plan below mitigates.

## Rationale

- **Authoritative for AEAT**: Frankfurter passes ECB rates through
  verbatim. Any AEAT audit that queries "which rate did you use on
  2026-04-15 USD→EUR?" gets the same number Frankfurter returns as
  the same number the ECB published that day.
- **Public + keyless**: no secret to rotate, no credential to leak.
  Zero attack surface for LOPDGDD purposes.
- **Business-day fallback**: Frankfurter's "closest prior business
  day" behaviour for weekends and holidays matches how the ECB
  itself treats those requests. The adapter preserves the response's
  reported date verbatim so callers who need strict-day semantics can
  verify `result.date == on` themselves (per the port contract).
- **Polite rate limiting**: 60 req/min buffers well below anything a
  small hobbyist wrapper could plausibly refuse. A full annual tax
  report is dozens of dates (one FX lookup per lot-consumption trade
  day), well inside the budget of a single-minute burst.

## Fallback plan — if Frankfurter goes down

The adapter is small (~250 LOC including error handling). The port
(`FxRateProviderPort`) is narrow: two methods, one return type. To
swap to ECB direct:

1. Add `hermes.adapters.fx.ecb::EcbXmlAdapter` implementing the same
   port.
2. Parse `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml`
   for latest; parse the historical ZIP
   (`eurofxref-hist-90d.xml` for recent, `eurofxref-hist.zip` for the
   full corpus) for `get_for_date`.
3. Estimated ~30 LOC of parsing + a small in-memory day-index cache
   to avoid re-downloading the historical file per lookup.
4. Wire selection: an env flag (`HERMES_FX__PROVIDER=frankfurter|ecb`)
   in the composite entry-point picks which adapter to construct. No
   caller code changes because both implement `FxRateProviderPort`.

## Daily-rate limitation (accepted for MVP1)

The FX layer resolves ONE rate per day, which is the ECB
reference-rate cadence and the AEAT expectation for tax filings. This
is fine for:

- Realised P&L calculations at tax-reporting time.
- End-of-day portfolio valuation.
- Historical trade EUR-cost stamps.

It is NOT sufficient for:

- Live P&L display during a trading day.
- Intraday risk metrics denominated in EUR.

Live/intraday EUR conversion is a distinct concern — a
`SpotPriceOracle` port (MVP2) will read exchange tickers for display,
explicitly labelled non-authoritative and never persisted as the tax
value. Keeping the two ports separate prevents "live display leaks
into the tax pipeline" — a class of bug that would be very hard to
audit out later.

## Consequences

- Adds `src/hermes/domain/fx/` (pure domain — `FxRate` value object
  with `convert()`), `src/hermes/ports/fx.py`
  (`FxRateProviderPort` Protocol), `src/hermes/adapters/fx/`
  (`FrankfurterAdapter` + owned rate limiter),
  `src/hermes/domain/errors.py::FxRateError` (new `InfraError`
  subclass), and one YAML file `config/frankfurter.yaml` under the
  existing `HERMES_FRANKFURTER__<FIELD>` env-var namespace.
- Zero new runtime dependencies — `httpx` is already installed.
- The rate limiter is duplicated from
  `hermes.adapters.exchanges.rate_limiter` because the two adapter
  packages must not cross-import (hex-arch boundary). If a third
  adapter needs a token bucket, promote to
  `adapters/_shared/rate_limiter.py` and delete both copies in the
  same PR.

## What would change this decision

- Frankfurter deprecates or goes down for extended periods → swap to
  ECB direct via the ~30 LOC change described above.
- AEAT clarifies that a specific vendor's reference rate (not the
  ECB's) is required for a particular filing → add a second adapter
  and route by filing type; keep Frankfurter as the default for
  everything else.
- Intraday EUR conversion becomes an MVP1 requirement (not currently
  planned) → introduce `SpotPriceOracle` as a distinct port; do NOT
  extend `FxRateProviderPort` to serve live prices.
