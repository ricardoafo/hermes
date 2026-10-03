# Calibration metrics M-09 and M-10, and the shadow Sharpe kill window

**Date**: 2026-10-03
**Author**: AI Bot on behalf of the Code Owner
**Related**: `docs/experiments/swing-mvp1/pre-registration-v1.md` §10;
[ADR-011](../architecture/adr-011-paper-trading-engine.md) D-19;
`docs/experiments/swing-mvp1/success-criteria.md` (draft); issues #360, #422

## Decision

The Code Owner agreed to three recommendations on 2026-10-03, in the "ADR-012
continuous AI evaluation" project thread:

1. **M-09 and M-10 thresholds follow ADR-011 D-19** (Accepted), not the draft
   success-criteria document. M-09: Pearson r > 0.85 and a bootstrap 95% CI
   lower bound above 0.70. M-10: |sim Sharpe − real Sharpe| < 0.3 over a
   rolling 30-day window.
2. **"Real" in shadow means live shadow compared with a backtest replay of
   the same days.** Shadow places no real orders, so M-09 and M-10 compare
   the live shadow run with the backtest engine replaying the same bars.
3. **The §10 shadow Sharpe kill uses a 30-day rolling Sharpe on daily
   returns, annualised with √365.** "Shadow Sharpe < 0.3 for 60 consecutive
   days" fires on the 60th consecutive UTC day with that Sharpe below 0.3. A
   day at or above 0.3, a day without 30 days of data, or a missing day
   restarts the count.

## Context

§10 says "Shadow Sharpe < 0.3 for 60 consecutive days" without naming the
Sharpe's window or scale, and ADR-011 and the success-criteria draft gave
different M-09 and M-10 thresholds. Choosing either would have reinterpreted
a protocol number, so it needed the Code Owner. The 0.3 floor and the 60 days
are unchanged; this fixes only how the Sharpe is measured.

The kill evaluator had a single 30-trade Sharpe check, which matches §10's
live-phase rule but would kill a shadow run up to 60 days early (#422). The
shadow rule now lives in its own streak counter.

## Consequences

- Automation sees only pass, warn or block for M-09 and M-10, and only "kill
  fired" for the Sharpe rule, never the values (ADR-012 D-06).
- No pre-registered number changes, so this is not a §11 deviation.
