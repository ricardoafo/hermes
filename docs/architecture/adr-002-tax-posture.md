# ADR-002: Tax posture — automated own-capital trading declared under the savings base, with dynamic FIFO in EUR

**Date**: 2026-08-20
**Status**: Accepted
**Accepted**: 2026-08-21
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: —

## Context

Hermes places trades on a Spanish tax resident's behalf. Two independent legal questions
must be answered before writing a single line of the engine:

1. **Is the activity taxable as capital gains (savings base, 19–28% rates) or as
   ordinary business income (*actividad económica*, general rates up to 47% plus IAE
   registration + quarterly filings)?**
2. **How must realized gains be computed?** Spain requires per-lot FIFO cost basis
   denominated in euros, regardless of the trading pair's native currency.

Getting either answer wrong is expensive. Ordinary-income treatment roughly doubles
the marginal rate. A FIFO calculation defective by even a fraction of a percent creates
correction liability plus late-payment interest (currently 4.0625% annual per Ley
General Tributaria art. 26.6). The Spanish tax authority (AEAT) has been increasing
enforcement pressure on crypto since Modelo 721 became mandatory (2023, deadline
2024-03-31 for the 2023 fiscal year).

The relevant regulatory backdrop:

- **DGT V2232-25** (Consulta Vinculante of 2025-11-24) — clarified that own-capital
  automated trading by an individual, without third-party mandate, does **not**
  automatically constitute *actividad económica*. This ruling is binding on AEAT for
  the facts as stated and is the pivot on which our posture rests.
- **Ley 35/2006 IRPF, art. 33-39** — capital-gains framework, FIFO obligation on
  homogeneous elements (art. 37.2).
- **Modelo 100** — annual IRPF declaration; realized crypto gains reported in savings
  base rows.
- **Modelo 721** — informative declaration of virtual currencies held abroad;
  mandatory if aggregate foreign-held crypto exceeds €50,000 on 31 December.
- **CDI España-Argentina** (BOE-A-1994-27310) — treaty allowing foreign-tax credit for
  Argentine tax paid on Argentine-source income (relevant because the user's Binance
  account is under Argentine KYC — see [ADR-004](./adr-004-exchange-decision.md)).
- **DGT V0999-18, V1069-19, V0975-22** — earlier bindings confirming crypto-to-crypto
  swaps are taxable events even without fiat leg.

## Decision

Hermes operates under the following tax posture:

1. **Savings base (base del ahorro), not actividad económica.** All realized gains are
   declared as capital gains under IRPF savings-base rates (2026 scale: 19% up to
   €6,000; 21% up to €50,000; 23% up to €200,000; 27% up to €300,000; 28% above).
2. **FIFO cost basis in EUR, per asset, computed dynamically per trade.** Every SELL
   consumes the oldest lots first at their euro-denominated cost basis at acquisition
   time. Schema and worked example live in
   [ADR-003 section 03](./adr-003-solution-architecture/03-persistence-schema.md).
3. **Every trade row stores EUR values from insert.** `price_eur`, `fee_eur`,
   `eur_rate` are mandatory columns, not "compute later". This is not a schema-hygiene
   preference — it is what makes the FIFO calculation audit-defensible.
4. **Modelo 100 filed annually, Modelo 721 filed only when the €50,000 threshold is
   crossed.** The engine emits both drafts automatically at year-end; the user reviews
   and files.
5. **CDI foreign-tax credit path is coded but expected inactive.** Argentine capital
   gains tax on foreign-source income is 0% for individuals under current ARG law;
   there is nothing to credit in the normal case. The path exists for the day that
   changes.
6. **Rate-recharacterization guard is enforced at engine level.** Trade frequency,
   position count, and portfolio structure are monitored against thresholds that would
   invite AEAT recharacterization as *actividad económica*. Exceeding a threshold
   emits an anomaly event and pauses new signals.

## Why

### Why savings base, not actividad económica

Three prongs support the savings-base posture:

**Prong 1: DGT V2232-25 explicitly covers this pattern.** The 2025-11-24 consulta
addresses an individual using automated software to trade their own capital on
regulated exchanges. It states that the activity does not automatically constitute
*actividad económica* even when: (a) trades are frequent, (b) trades are software-
initiated, (c) the individual has technical knowledge. The pivotal factors AEAT
weighs are ordering habitual + third-party account activity + business
infrastructure — none of which apply here.

**Prong 2: The engine trades own capital exclusively.** No custodian relationship,
no third-party mandate, no fund structure, no fee income. Hermes never trades
somebody else's account. This removes the strongest single indicator of
*actividad económica* (habitual provision of services to third parties).

**Prong 3: Trade frequency is bounded by design.** The rebalancer trades a handful
of times per year (threshold band + ~5% deadband + 60/30/10 core). The satellite
trades few times per month (Donchian breakouts with ATR-based stops). Total trade
count is projected at 30-100 per year through 2027 — well under the informal
"habitual" threshold AEAT applies (~500-1000 trades/year in practice, though
undefined by law).

### Why FIFO, not LIFO or average cost

Ley 35/2006 IRPF art. 37.2 mandates FIFO for "elementos patrimoniales
homogéneos". BTC-A and BTC-B held by the same taxpayer are homogeneous. LIFO
and weighted-average are not permitted for individuals. The choice is not one to
debate — it is the law.

### Why EUR from row 1 (not "compute later from USDT + ECB rate")

If we store only native-currency prices (USDT) and reconstruct EUR at declaration
time, we hit:

- **ECB reference-rate gaps** — no rate on weekends and Spanish bank holidays; the
  Bank of Spain publishes only working-day rates.
- **Rate revisions** — ECB reference rates are revised historically (rare, but
  observable).
- **Timing uncertainty for weekend trades** — a Saturday trade uses which rate?
  Friday's? Monday's? AEAT has not published guidance.
- **Multiple defensible EUR conversion sources** — Binance's native BTC/EUR pair
  differs from `BTC/USDT × EUR/USDT` differs from Coingecko's aggregated rate.
  Choosing one at trade time locks in the audit trail.

By storing `price_eur` and `eur_rate` at trade insertion, the FIFO calculator is a
pure sum. Audit-defensibility is the property we optimize for.

The EUR conversion source itself (Binance native EUR pair vs two-hop via USDT) is
resolved in ADR-004.

### Why the recharacterization guard exists in code

AEAT's *actividad económica* recharacterization uses an unenumerated set of
factors ("indicios"). No single trade count, position size, or infrastructure
signal triggers it deterministically. The known signals include:

- Very high trade frequency (hundreds per month sustained)
- Dedicated business infrastructure (rented server presented as workplace, employees)
- Marketing the activity to third parties
- Fee-generating structures (custody fees, performance fees)
- Formal business registration (IAE)

Hermes cannot control what AEAT will do. It can, however, ensure that the observable
signals stay firmly on the "individual investor" side of any plausible line. The
guard raises an anomaly and pauses new signal generation if any of the following
holds over a rolling 90-day window:

| Signal              | Threshold        | Rationale                                                                  |
| ------------------- | ---------------- | -------------------------------------------------------------------------- |
| Trade count         | > 500 in 90 days | Approaches the informal AEAT "day trader" range; well beyond design intent |
| Distinct assets     | > 12 concurrent  | 6 (planned) is well-diversified; 12+ suggests service-provider structure   |
| Cross-tenant trades | > 0 ever         | Hermes never trades another party's capital by design; nonzero is a bug    |
| Fee income          | > €0 ever        | Hermes charges nobody; nonzero suggests infrastructure recharacterization  |

Values are stored in `config/tax_guard.yaml` (not hardcoded — see ADR-003
invariant I-05) so they can be tightened as regulation evolves without a
code change.

## Consequences

### Positive

- **Marginal rate roughly halved** vs *actividad económica* treatment (28% top
  savings-base vs ~47% top general-rate marginal).
- **No IAE registration, no quarterly Modelo 130/131/303, no autónomo fee.** IRPF
  once a year and (if applicable) Modelo 721 once a year.
- **FIFO calculation is deterministic and testable.** Property-based tests can
  assert per-trade invariants; snapshot tests can pin end-of-year numbers.
- **Every trade is audit-defensible in isolation.** A single row has trade,
  consumed lots, realized gain, EUR rate at trade time. No reconstruction needed.

### Negative

- **DGT V2232-25 is binding only for its stated facts.** If our facts drift
  (e.g. the engine starts managing a friend's account), the ruling stops
  protecting us. This is the largest single risk — mitigated by the
  recharacterization guard and by ADR-001 (LLM never trades).
- **Modelo 721 kicks in as capital grows.** At the projected €50k+ crypto AUM
  in 2027-2028, the €50,000 threshold triggers and Modelo 721 must be filed
  by 2027-03-31 for FY 2026 or 2028-03-31 for FY 2027, depending on the year
  the threshold is first crossed on 31 December.
- **Argentine-source income posture requires the Binance account to remain
  ARG-KYC'd.** If Binance forces a residency update, the offshore-income
  path collapses and we would need to switch primary to Kraken EU (already
  hedged in ADR-004).
- **Rate scale is not stable.** Spain has been progressively raising
  savings-base rates (2020: 26% top; 2024: 28% top). Assume further
  upward pressure over the project lifetime.

### Neutral

- **The dynamic FIFO calculator is a pure function of `trades` + `tax_lots` +
  `lot_consumptions`.** It has no side effects and can be re-run at any time
  to produce an updated declaration. This is the calculator's design property,
  not a happy accident.
- **The `tax_lots.remaining_qty` column is the FIFO cursor.** After each SELL,
  the calculator consumes oldest-first until quantity is satisfied and writes
  `lot_consumptions` rows recording exactly which lot lost how much and at
  what realized gain. All state is on disk; nothing is inferred.
- **A pending user obligation exists: gestor consultation on Modelo 721 for
  ARG-Binance situation.** Not blocking for the engine build, but should be
  resolved before the first FY-end declaration.

## What would change this decision

- **DGT publishes a new consulta narrowing V2232-25.** If AEAT successfully
  argues that automated software trading is per se *actividad económica*, we
  must consider IAE registration, autónomo status, and general-rate declaration.
  Rewriting the guard and adjusting the entity structure would be substantial.
- **Trade frequency doubles beyond design.** If the recharacterization guard
  fires repeatedly, the engine parameters need tightening (widen the rebalance
  band, tighten the swing entry conditions) rather than declaring at higher rates.
- **Wealth reporting rules change (Impuesto sobre el Patrimonio / Impuesto de
  Solidaridad).** These are unrelated to income tax but might affect the
  reporting cadence and residency planning. The engine's role is limited to
  producing year-end balance snapshots on demand.
- **Argentina imposes source tax on non-resident capital gains.** Currently 0%
  for individuals. If ARG changes this, the CDI foreign-tax credit path
  (currently coded but inactive) activates.

## References

- DGT V2232-25 (Consulta Vinculante 2025-11-24) — automated own-capital trading
  and its non-classification as *actividad económica*
- Ley 35/2006 IRPF, art. 33-39 — capital-gains framework
- Ley 35/2006 IRPF, art. 37.2 — FIFO obligation on homogeneous elements
- Modelo 100 instructions (AEAT) — savings-base rows for crypto
- Modelo 721 instructions (AEAT) — foreign-held virtual currency declaration
- CDI España-Argentina (BOE-A-1994-27310) — foreign-tax credit mechanism
- DGT V0999-18, V1069-19, V0975-22 — crypto-to-crypto swap taxability

## Cross-references

- [ADR-001](./adr-001-llm-as-supervisor.md) — LLM does not trade (reinforces
  own-capital posture)
- [ADR-003 section 03](./adr-003-solution-architecture/03-persistence-schema.md) —
  FIFO schema + worked example
- [ADR-004 — Exchange decision (ARG-KYC posture + EUR pricing source)](./adr-004-exchange-decision.md)

## Pending

- **Gestor consultation** — user to confirm CDI mechanics + Modelo 721 for the
  ARG-Binance situation before first FY-end declaration. Not blocking engine build.
- **Recharacterization thresholds** — the values in `config/tax_guard.yaml` are
  initial estimates. First review after 90 days of shadow-mode data.
