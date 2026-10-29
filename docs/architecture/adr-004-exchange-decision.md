# ADR-004: Exchange decision — Binance primary, Kraken hot backup, agnostic port at the code boundary

**Date**: 2026-08-20
**Status**: Accepted — partially superseded by [ADR-014](adr-014-pre-registration-binds-real-capital.md) (real-capital dates only)
**Accepted**: 2026-08-21
**Deciders**: Maintainer
**Supersedes**: —

## Context

Hermes needs an exchange for market data and, later, for orders. The choice has to hold up
against regulatory change as well as technical change. In mid-2026 Binance withdrew its MiCA
application in the EU and restricted spot services for some EU-resident retail accounts. Which
accounts an exchange serves, and on what terms, can change at short notice.

The project must also be resilient against **any single-exchange failure mode**:

- the exchange closes or restricts the account;
- the exchange re-enters a market under new terms that conflict with how Hermes runs;
- API instability or unilateral endpoint deprecation (has happened before);
- regulatory action outside the exchange's control.

The engineering hedge for these is not another exchange in production. It is a
**semantic port** at the code boundary, so a second exchange can be brought up in days, not
weeks. The operational hedge is a **cold-but-ready backup account** that can be warmed to hot
quickly.

## Decision

Hermes uses a three-tier exchange posture:

1. **Primary: Binance.** All read and write traffic goes through the Binance adapter by default.
   Market data comes from Binance's public endpoints.
2. **Semantic `ExchangePort` at the code boundary.** All application-layer code depends on the
   port, not on Binance. A second adapter (Kraken) is stubbed from day one and passes contract
   tests against Kraken's test environment before shadow mode begins. The technical interface
   is specified in [ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md).
3. **Cold backup: Kraken, verified but idle.** The account holds nothing in normal operation.
   The runbook [`docs/runbook/failover-to-kraken.md`](../runbook/failover-to-kraken.md) lists
   every action required to fail over: switch config, verify API credentials, transfer capital,
   run reconciliation.
4. **Fee schedule per exchange lives in config, never in code.** `config/fees.yaml` holds
   taker/maker basis points per exchange per tier. The engine reads the active exchange's fee
   schedule for its min-notional and profitability decisions.
5. **EUR pricing source is documented per exchange.** For Binance, the choice (Binance
   `BTC/USDT` × ECB `EUR/USDT` reference rate) is locked in
   [ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md). Any future
   adapter must document its EUR source before promotion to primary.
6. **A written failover runbook exists and is tested annually.** The drill cuts Kraken over to
   primary in a test environment, runs one full cycle and captures wall-clock time. Target:
   under 90 minutes end-to-end.

## Why

### Why Binance stays primary

1. **Liquidity and order types.** Binance has some of the deepest spot books and supports
   every order type the engine needs.
2. **Data continuity.** The backtest data and the live market data come from the same venue,
   so research and paper trading see the same prices.
3. **Migration cost is real.** Moving primary means re-verifying an account elsewhere,
   transferring capital (with its own realised-gain events) and losing access to the deeper
   books. None of this is catastrophic, but none of it is free either.
4. **The hedges make the choice reversible.** If Binance stops being usable, the port and the
   backup account turn a forced move into a documented procedure.

### Residual risks we accept

- **Account terms can change.** An exchange can restrict or close an account when its rules or
  its regulators change. The backup account and the runbook bound the impact.
- **Regulation keeps moving.** MiCA authorisations, reporting rules and capital-control regimes
  change over time. Each change is answerable with a documented failover, not panic engineering.

### Why an agnostic port at the code boundary

Regardless of the primary-exchange choice, the codebase cannot bet on any one API surviving
unchanged. Concrete failure modes we hedge against:

- Binance deprecates an endpoint version (it has happened three times in five years: margin
  API v1, futures API v2, stream envelope v1).
- Binance changes its fee tier structure (mid-2023, retroactive).
- Binance introduces a mandatory feature we don't want (for example extra verification for
  higher tiers).
- Regulatory action forces an immediate cutover (rare, but the class of event that ended the
  FTX era for many).

The `ExchangePort` (defined in
[ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md)) insulates
application code from every one of these. When any of them happens, the fix is in one adapter
file, not scattered across the codebase.

### Why Kraken specifically as the backup (not Coinbase, Bitfinex, Bitvavo)

| Criterion             | Kraken                                | Coinbase                      | Bitfinex             | Bitvavo (NL)             |
| --------------------- | ------------------------------------- | ----------------------------- | -------------------- | ------------------------ |
| MiCA status (2026-08) | Compliant, EU-authorized              | Compliant, EU-authorized      | Not EU-authorized    | Compliant, EU-authorized |
| Spot BTC EUR spread   | Tight (top-3 EUR liquidity)           | Wider spreads on non-USD      | Not EU-authorized    | Competitive but thinner  |
| API stability         | Excellent, well-documented            | Excellent                     | Historical incidents | Adequate                 |
| API ergonomics        | Reasonable, REST + WebSocket          | Good, better docs than Kraken | Idiosyncratic        | Reasonable               |
| Order-type parity     | Market, limit, stop-loss, take-profit | Limit-primary; stop-loss weak | Full but arcane      | Market, limit, stop-loss |
| Fee model             | Volume-tier, transparent              | Higher taker fees             | Volume-tier          | Volume-tier              |

Kraken wins on the combination of MiCA compliance, EUR liquidity, order-type parity and API
quality. Coinbase's stop-loss support is a real gap for ATR-based trailing stops. Bitfinex is
not MiCA-authorized. Bitvavo is a viable second candidate; Kraken is chosen for the deeper EUR
books.

### Why the fee schedule is config, not constant

- Fees change (Binance raised BNB-discount thresholds in 2024, Coinbase introduced new tiers in
  2025). A code change per fee update is friction we don't need.
- The min-notional and profitability guards read fees at runtime. Hardcoding them means the
  tests describe an economic reality that no longer exists.
- Per-exchange means Kraken's fee schedule already exists as data on the day we cut over; the
  cutover doesn't require a code change to a constants file.

`config/fees.yaml` is versioned in git. Changes are PR'd and reviewed.

## Consequences

### Positive

- **Regulatory optionality.** A hostile decision by an exchange or a regulator is answerable
  with a documented failover.
- **The hedge is cheap.** An idle backup account costs nothing to hold. Contract tests for
  Kraken run in CI, so the stub is exercised on every PR.
- **Fee changes are declarative.** Updating a fee schedule is a YAML PR, not a code change,
  and auditable via git blame.
- **EUR pricing decision is documented per exchange.** Any adapter promoted to primary must
  specify its EUR conversion source and how it handles weekend/holiday rate gaps. No implicit
  choices.

### Negative

- **Two adapters means two sets of contract tests.** Kraken's contract-test suite must run in
  CI even though the adapter is idle in production, or the fast-failover claim is a fiction.
- **Fee-schedule drift risk.** If `config/fees.yaml` is not kept up to date, the min-notional
  guard understates real cost and the profitability guard overstates it. It needs a refresh
  cadence: quarterly review, or an immediate PR on an exchange fee-change announcement.
- **Kraken's `XBT/USD` naming and different rate-limit model are extra edges in the adapter
  code.** Symbol translation, rate-limit accounting and stop-loss semantics differ enough that
  Kraken's adapter is a peer implementation, not a trivial subclass of Binance's.

### Neutral

- **Both adapters implement the same `ExchangePort`.** The port is defined once; adapters live
  under `src/hermes/adapters/exchanges/`. Adding a third exchange later is additive, not
  disruptive.
- **The runbook is a documentation artifact, not code.** Its correctness is asserted by the
  annual failover drill.
- **Testing against test environments does not test real fills.** Contract tests cover the wire
  protocol; shadow-mode data is what validates the full loop.

## What would change this decision

- **The primary account becomes unusable.** Cutover to Kraken becomes immediate, not standby.
  The runbook is executed, and this ADR is rewritten with Kraken as primary.
- **Reporting rules change in a way the current setup can't meet.** Same cutover.
- **Binance is re-authorised under MiCA.** That reopens the question of which Binance entity to
  use. It is a follow-up ADR at that time; no code change either way.
- **A third exchange with materially better economics emerges.** Additive adapter, not a
  decision reversal.

## Cross-references

- [ADR-002](./adr-002-tax-posture.md) — the tax posture: FIFO in EUR, Modelo 100 and 721
- [ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md) — technical
  interface, retry policy, EUR pricing method
- [ADR-003 section 09](./adr-003-solution-architecture/09-deployment.md) — credential
  management for API keys (systemd LoadCredentialEncrypted)
- [ADR-003 section 10](./adr-003-solution-architecture/10-failure-recovery.md) — reconciliation
  flow that runs post-failover

## Pending

- **Fee-schedule refresh cadence** — proposed quarterly; to be formalised in a decision journal
  entry.
