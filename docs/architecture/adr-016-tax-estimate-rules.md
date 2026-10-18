# ADR-016: Tax estimate rules — per-year savings scale from config (30% top band since 2025), fees in FIFO values, measurement only

**Date**: 2026-10-03
**Status**: Accepted (merged in PR #401)
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: [ADR-002](adr-002-tax-posture.md) Decision item 1, the rate-scale sentence only ("2026 scale: 19% up to
€6,000; 21% up to €50,000; 23% up to €200,000; 27% up to €300,000; 28% above"). Everything else in ADR-002 stands.

---

## Context

ADR-002 puts hermes gains in the IRPF savings base and fixes the 2026 rate
scale in its text, with a 28% top band. Since tax year 2025 the top band above
€300,000 is 30% (Ley 7/2024, reflected in the AEAT IRPF 2025 manual). ADR-002
is Accepted, so the correction needs a new ADR (ADR-009 lifecycle, AGENTS.md
rule 10).

The operator console (#377) and the tax module (#395) will show estimated tax
on shadow and, later, real gains. ADR-002 leaves open several rules those
estimates need: how fees enter the FIFO values, what counts as the tax year,
how USDT is treated, how losses net and carry forward, how the hermes share is
computed against the operator's other income, and what the two-month
repurchase rule does. On 2026-10-03 the Code Owner settled them.

Facts in the code today (origin/main, 2026-10-03):

- The `trades` table has no EUR columns, although ADR-002 §3 requires them.
- `SimulatedExchangeAdapter._open_lot` stores the USDT fill price as the lot's
  EUR cost basis (`src/hermes/adapters/exchanges/simulated.py`).
- `LotConsumption.record` computes gain without fees
  (`src/hermes/domain/lots/lot_consumption.py`).

Those code gaps are fixed in #386; this ADR records the rules that fix follows.

## Decision

1. **The savings scale comes from config, per tax year.** `config/tax_scales.yaml`
   holds the bands for each tax year; code never hardcodes a rate. Tax years 2025
   and 2026: 19% up to €6,000; 21% up to €50,000; 23% up to €200,000; 27% up to
   €300,000; 30% above. The savings scale is national; regions cannot change it.
   A year with no scale fails closed ("scale missing for <year>"), never a guess.
2. **The tax year is the calendar year in Europe/Madrid time.** A fill at 23:30
   UTC on 31 December counts in the next year.
3. **Fees enter the FIFO values.** Acquisition value = EUR price × quantity + EUR
   buy fee. Transmission value = EUR price × quantity − EUR sell fee (IRPF art. 35).
   The gain of a consumption is transmission value minus the acquisition value
   of the lots it consumes.
4. **USDT is its own FIFO asset.** Selling BTC for USDT disposes of BTC; buying
   BTC with USDT disposes of USDT. Shadow start capital opens one USDT lot at the
   start-date rate.
5. **EUR rate.** The ECB EUR/USD reference rate for the fill date, or the prior
   business day on weekends and holidays, fetched through the existing FX port.
   USDT is treated as USD; the ECB publishes no EUR/USDT rate, so this is the
   working reading of ADR-004's "ECB EUR/USDT reference rate". A failed lookup
   never blocks a fill: the row is stored as pending and backfilled.
6. **Netting and carry-forward.** Gains and losses from transmissions in a year
   net against each other. A net loss offsets up to 25% of positive savings
   capital income (interest, dividends), and the reverse also applies. What is
   left carries forward 4 years, oldest first.
7. **Hermes tax is marginal.** Tax(savings base with hermes) minus tax(savings
   base without hermes), using the operator's taxpayer profile (other savings
   income, other gains, carried losses, and general income for the
   personal-minimum edge case). With no profile, other income is zero and the
   output says so. Shadow estimates use the real profile.
8. **The two-month repurchase rule is flagged, not applied.** Art. 33.5.f is
   written for listed securities and crypto is not one. A loss sale with a
   same-coin purchase within two months either side is flagged for the gestor;
   the loss is not deferred.
9. **Tax outputs are measurement only.** Nothing the tax module computes (tax,
   flags, tax if sold now) is read by the strategy, sizing, signals, risk or kill
   rules. An import-linter contract enforces it (#395). This keeps the shadow
   test unchanged by the measurement.
10. **Every output is an estimate.** Each figure is labelled an estimate, not a
    filing; shadow figures also say "shadow (no real tax due)".
11. **The total IRPF view is optional.** Region-aware general-base scales per year
    come from config, the profile names the region, and the view is off by
    default (#396). It never changes the hermes tax figure.
12. **The taxpayer profile stays local.** It lives outside the repo
    (`~/.config/hermes/taxpayer.yaml`, path overridable by env var), is never
    committed, never logged, and never reaches Slack, Sentry or any AI-facing
    tool (#394).

## Why

- **Scale in config:** the top savings rate has risen from 26% to 28% to 30%
  since 2020 (ADR-002 Consequences, Ley 7/2024). A per-year table turns the next change into a config
  edit with a test, not a code change or another superseding ADR.
- **Fees and USDT as an asset:** both follow the law as written. Ignoring fees
  overstates gains; treating USDT as EUR cash hides small EUR/USD gains and
  losses on every swap. The estimate should match what a gestor would file.
- **Marginal tax:** the hermes band depends on the operator's other savings
  income and carried losses. A flat 19% would understate tax for anyone above
  the first band.
- **Flag, not defer:** deferring losses on a contested reading would change
  numbers the operator relies on; a flag keeps the decision with the gestor.
- **Measurement only:** the shadow test measures a pre-registered strategy. If
  tax figures could steer sizing or exits, the test would measure something
  else.

## Consequences

### Positive

- Tax estimates follow one written rule set, with every rate in config.
- The console can show gross, fees, estimated tax and net that a gestor would
  recognise.

### Negative

- USDT lots add rows and small EUR/USD gains and losses to every swap.
- The USDT ≈ USD approximation is wrong during a de-peg; the estimate inherits
  that error.
- The operator must keep a local profile up to date for the marginal figure to
  be right.

### Neutral

- Filing (Modelo 100/721) is unchanged and stays in MVP2 (#36).

## What would change this decision

- A new savings scale: add the year to `config/tax_scales.yaml`; no new ADR.
- AEAT or a court applying the two-month rule to crypto: switch from flag to
  defer, by a new ADR.
- A gestor advising a different EUR source or USDT treatment: new ADR.

## Cross-references

- [ADR-002](adr-002-tax-posture.md) — tax posture (rate-scale sentence superseded here)
- [ADR-004](adr-004-exchange-decision.md) — EUR pricing source
- [ADR-007](adr-007-testing-strategy.md) — test tiers for the tax domain
- Issues: #386 (EUR values and fees in FIFO), #394 (profile and scales), #395 (tax estimates), #396 (total IRPF view)

## Pending

- None. The operator's region for the total IRPF view is Región de Murcia (confirmed 2026-10-03, #396).
