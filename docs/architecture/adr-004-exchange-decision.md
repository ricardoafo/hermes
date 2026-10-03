# ADR-004: Exchange decision — Binance primary via ARG-DNI, Kraken hot backup, agnostic port at the code boundary

**Date**: 2026-08-20
**Status**: Accepted — partially superseded by [ADR-014](adr-014-pre-registration-binds-real-capital.md) (real-capital dates only)
**Accepted**: 2026-08-21
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: —

## Context

On 2026-06-24 Binance withdrew its MiCA application in the EU, and on 2026-07-01
suspended spot services for EU-resident retail accounts (Spain, France, Italy,
Poland, Portugal, Ireland, and the remainder of the EU27 in scope). This is a
material change to the platform assumption the project originally rested on. Any
Spain-resident opening a new Binance account after that date is blocked or
throttled to withdrawal-only.

The user, however, holds a **long-standing Binance account under Argentine DNI
KYC** with Argentine-only banking rails and Argentine residency on file. The
account predates the EU lockout by years and is factually an Argentine account
even though the user is currently a Spanish tax resident. Deposits and
withdrawals never touch a Spanish bank; the exchange TOS is compatible with an
Argentine-resident user, and the Spain-Argentina CDI (BOE-A-1994-27310) is the
tax vehicle for declaring offshore-source gains in Spain (see [ADR-002](./adr-002-tax-posture.md)).

At the same time, the project must be resilient against **any single-exchange
failure mode**:

- Binance re-tightens residency enforcement and closes the account
- Binance re-enters the EU under MiCA and imposes conflicting terms
- API instability or unilateral endpoint deprecation (has happened before)
- Regulatory action outside Binance's control (OFAC, further EU rulings)

The engineering hedge for these is not another exchange in production — it is a
**semantic port** at the code boundary so a second exchange can be brought up
in days, not weeks. The operational hedge is a **cold-but-ready Kraken account**
that can be warmed to hot in ~20 minutes of user work.

## Decision

Hermes uses a three-tier exchange posture:

1. **Primary: Binance, connected via the user's ARG-DNI account.** All read and
   write traffic goes through the Binance adapter by default.
2. **Semantic `ExchangePort` at the code boundary.** All application-layer code
   depends on the port, not on Binance. A second adapter (Kraken) is stubbed
   from day one and passes contract tests against Kraken testnet before shadow
   mode begins. The technical interface is specified in
   [ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md).
3. **Cold backup: Kraken account KYC'd but idle.** User completes Kraken EU KYC
   before Nov-2026 real-capital deployment (~20 min work, €0 cost). The account
   holds €0 in normal operation. Documented runbook `docs/runbook/failover-to-kraken.md`
   (pending) captures every action required to fail over: switch config, verify
   API credentials, transfer capital, run reconciliation.
4. **Fee schedule per exchange lives in config, never in code.** `config/fees.yaml`
   holds taker/maker basis points per exchange per tier. The rebalancer reads the
   active exchange's fee schedule to make min-notional and profitability
   decisions.
5. **EUR pricing source is documented per exchange.** For Binance, the choice
   (Binance `BTC/USDT` × ECB `EUR/USDT` reference rate) is locked in
   [ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md).
   Any future adapter must document its EUR source before promotion to primary.
6. **A written failover runbook exists, is tested annually.** The failover
   drill is a scheduled task: cutover Kraken to primary in a test environment,
   run one full rebalance, capture wall-clock time. Target: under 90 minutes
   end-to-end.

## Why

### Why Binance stays primary despite MiCA

Four factual observations, each independently sufficient to justify keeping
the existing account:

1. **The account is legally Argentine.** KYC file, deposit rail, withdrawal
   rail, source-of-funds documentation — all Argentine. Nothing about the
   account is EU-resident. The MiCA-triggered lockout targets *EU-resident*
   accounts by design; the KYC-of-record is what the exchange enforces on.
2. **The offshore-income declaration path is a mature, well-understood
   posture in Spanish tax law.** Spanish residents declare worldwide income on
   Modelo 100; the CDI credits any Argentine tax paid (currently 0% on foreign-
   source capital gains for individuals). This is analogous to the user's
   existing Merval (Argentine equities) declaration, which has been filed
   without issue for years.
3. **The migration cost is real.** Moving to a fully-EU MiCA-compliant
   exchange (Kraken EU, Bitvavo, Bitfinex) means: closing Binance account
   (loses order history + tax lots), re-KYC'ing at new exchange, capital
   transfer with associated realized-gain events, retraining muscle memory,
   and losing access to Binance's deeper spot books. None of these are
   catastrophic — but neither is any of them free.
4. **Binance is not committing to permanent EU exit.** Re-application under
   MiCA is an active corporate agenda item and is plausible before Nov 2026
   real-capital deployment. If it re-enters, the hedge decision becomes
   moot; if it does not, our hedges are already in place.

### Why the ARG-DNI approach has real risks (and we accept them)

The user accepts the following residual risks with explicit acknowledgment:

- **Binance TOS residency-update clause.** Binance TOS technically requires
  updating residency on change of jurisdiction. The user's Spanish IP
  addresses accessing an ARG-KYC account is enforceable at Binance's
  discretion. Historically enforcement targets new EU-registrations and
  Spanish-bank activity — the user's Argentine-bank-only pattern avoids the
  latter trigger. Enforcement risk is nonzero but low-observable.
- **Monotributo does not cover crypto capital gains in Argentina.**
  Argentine-side declaration flows through personal capital gains (currently
  0% for individuals on foreign-source income) or autónomo, not Monotributo.
  The user acknowledges this and does not rely on Monotributo for the
  activity.
- **Argentine capital-controls regime may change.** ARG has periodically
  imposed capital controls affecting crypto withdrawals. Not currently in
  force for individual retail; would be observable via news before impacting
  operations.
- **Withdrawals to Spanish banks require an intermediate ARG-bank step,
  producing a paper trail.** Accepted as fine; makes the offshore-income
  declaration cleaner, not messier.

### Why an agnostic port at the code boundary

Regardless of the primary-exchange choice, the codebase cannot bet on any one
API surviving unchanged. Concrete failure modes we hedge against:

- Binance deprecates an endpoint version (has happened three times in the past
  five years — margin API v1, futures API v2, stream envelope v1)
- Binance changes fee tier structure (mid-year 2023, retroactive)
- Binance introduces mandatory feature we don't want (e.g. VIP verification
  for higher tiers, KYC-2)
- Regulatory action forces immediate cutover (rare, but the class of event
  that ended the FTX era for many)

The `ExchangePort` (defined in
[ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md))
insulates application code from every one of these. When any of them happens,
the fix is in one adapter file, not scattered across the codebase.

### Why Kraken specifically as the hot backup (not Coinbase, Bitfinex, Bitvavo)

| Criterion             | Kraken                                | Coinbase                      | Bitfinex               | Bitvavo (NL)             |
| --------------------- | ------------------------------------- | ----------------------------- | ---------------------- | ------------------------ |
| MiCA status (2026-08) | Compliant, EU-authorized              | Compliant, EU-authorized      | Not EU-authorized      | Compliant, EU-authorized |
| Spot BTC EUR spread   | Tight (top-3 EUR liquidity)           | Wider spreads on non-USD      | Not EU-authorized      | Competitive but thinner  |
| API stability         | Excellent, well-documented            | Excellent                     | Historical incidents   | Adequate                 |
| API ergonomics        | Reasonable, REST + WebSocket          | Good, better docs than Kraken | Idiosyncratic          | Reasonable               |
| Order-type parity     | Market, limit, stop-loss, take-profit | Limit-primary; stop-loss weak | Full but arcane        | Market, limit, stop-loss |
| Fee model             | Volume-tier, transparent              | Higher taker fees             | Volume-tier            | Volume-tier              |
| Existing user account | User does not have one                | User does not have one        | User does not have one | User does not have one   |

Kraken wins on the combination of MiCA compliance, EUR liquidity, order-type
parity, and API quality. Coinbase's stop-loss support is a real gap for the
Donchian satellite (ATR-based trailing stops). Bitfinex is not MiCA-authorized.
Bitvavo is a viable second candidate; Kraken is chosen for the deeper EUR books.

### Why the fee schedule is config, not constant

- Fees change (Binance raised BNB-discount thresholds in 2024, Coinbase
  introduced new tiers in 2025) — a code change per fee update is
  friction we don't need.
- The rebalancer's min-notional and profitability guards read fees at runtime
  — hardcoding them means the tests are lying about the current economic
  reality.
- Per-exchange means Kraken's fee schedule already exists as data on the day
  we cutover; the cutover doesn't require a code change to a constants file.

`config/fees.yaml` is versioned in git. Changes are PR'd and reviewed.

## Consequences

### Positive

- **Regulatory optionality.** A hostile decision on either side (Spain
  tightens on ARG-KYC accounts; Binance forces residency update; Argentina
  imposes capital controls) is answerable with a documented failover, not
  panic engineering.
- **Cost of the hedge is trivial.** Kraken KYC is ~20 minutes of user time
  and €0 in idle balance. Contract tests for Kraken run against testnet in
  CI; the stub is exercised on every PR.
- **Fee changes are declarative.** Updating a fee schedule is a YAML PR, not
  a code change. Auditable via git blame.
- **EUR pricing decision is documented per exchange.** Any adapter promoted
  to primary in the future must specify its EUR conversion source and how it
  handles weekend/holiday rate gaps. No implicit choices.

### Negative

- **Two adapters means two lots of contract tests.** Kraken's contract-test
  suite must run in CI even though the adapter is idle in production, or
  the "20-minute failover" claim is a fiction.
- **Fee-schedule drift risk.** If `config/fees.yaml` is not kept up to
  date, the min-notional guard understates real cost and the profitability
  guard overstates it. Requires a manual refresh cadence — proposed:
  quarterly review, or immediate PR on exchange fee-change announcement.
- **ARG-DNI posture is undocumented publicly.** There is no case law
  answering "is a Spanish resident with an ARG-KYC exchange account
  compliant with Binance TOS?" The user carries this ambiguity.
- **Kraken's `XBT/USD` naming and different rate-limit model are extra
  edges in the adapter code.** Symbol translation, rate-limit accounting,
  and stop-loss semantics differ enough that Kraken's adapter is not a
  trivial subclass of Binance's — it is a peer implementation.

### Neutral

- **Both adapters implement the same `ExchangePort`.** The port is defined
  once, in the domain-adjacent module; adapters live under
  `src/hermes/adapters/exchanges/`. Adding a third exchange later is
  additive, not disruptive.
- **The runbook is a documentation artifact, not code.** Its correctness
  is asserted by the annual failover drill.
- **Testing against testnets does not test real fills.** Contract tests
  cover the wire protocol; shadow-mode data (Aug-Nov 2026 in Binance
  production, tiny sums) is what validates the full loop.

## What would change this decision

- **Binance forces the user's account to update to Spanish residency.**
  Cutover to Kraken becomes immediate, not standby. The runbook is
  executed; ADR-004 is rewritten with Kraken as primary.
- **Spain introduces reporting requirements incompatible with the
  ARG-KYC posture.** For example, Modelo 721 revisions that require
  KYC-jurisdiction attestation matching residency. Same cutover.
- **MiCA-compliant EU jurisdiction becomes available for Binance.**
  Binance re-authorized in Ireland or the Netherlands re-opens the
  question of whether to migrate to that entity or stay on the ARG
  account. Decision is a follow-up ADR at that time; no code change
  either way.
- **User's Argentine residency changes** (permanent move away from
  Argentina + change of KYC). Same as above — the ARG-KYC posture
  collapses, cutover to a MiCA-authorized exchange is required.
- **A third exchange with materially better economics emerges.**
  Currently Binance and Kraken dominate EUR liquidity outside the US.
  Additive adapter, not a decision reversal.

## Cross-references

- [ADR-002](./adr-002-tax-posture.md) — the tax posture that makes the
  ARG-KYC account viable (offshore-income declaration on Modelo 100 + CDI
  foreign-tax credit path)
- [ADR-003 section 04](./adr-003-solution-architecture/04-exchange-adapters.md)
  — technical interface, retry policy, EUR pricing method
- [ADR-003 section 09](./adr-003-solution-architecture/09-deployment.md) —
  credential management for API keys (systemd LoadCredentialEncrypted)
- [ADR-003 section 10](./adr-003-solution-architecture/10-failure-recovery.md) —
  reconciliation flow that runs post-failover

## Pending

- **User to confirm Binance KYC residency setting shows Argentina.**
  Screenshot into `docs/decisions/2026-08-XX-binance-kyc-verification.md`
  (redacted). Not blocking the engine build, but a prerequisite for the
  posture holding up.
- **User to KYC Kraken EU (~20 min).** Prerequisite for shadow-mode
  handoff testing. Not blocking Phase 0 (scaffolding).
- **Failover runbook** — `docs/runbook/failover-to-kraken.md` to be
  written as part of ADR-008 (Observability + Runbooks) or as a
  standalone artifact — to be decided.
- **Fee-schedule refresh cadence** — proposed quarterly; to be
  formalized in a decision journal entry.
