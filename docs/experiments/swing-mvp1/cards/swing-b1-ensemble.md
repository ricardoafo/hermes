---
card_id: swing-b1-ensemble
idea_family: breakout-ensemble
batch: first research batch (issue #1062)
status: DRAFT
---

# Card: multi-lookback breakout ensemble

## Mechanism

Trend-following returns in liquid futures and crypto come from several horizons at once, and a single breakout
length is one noisy sample of them (time-series momentum literature; the lab scan of 2026-10-06). Entering only when
breakouts over several lengths agree should cut the false single-length breakouts that the v2 post-mortem infers
behind its failed bear stretch.

## Rules

- Entry (long only): on a 4h bar close, count the lookbacks L in {20, 55, 120} 4h bars for which the close is above
  the highest high of the previous L bars. Enter when the count is at least K, and the daily close is above the daily
  EMA(200), as in v2.
- Exit: v2's chandelier stop, time stop and regime break, priced at the bar close (v2 §5.2, §6.2).

## Free parameters (3)

| Parameter | Base | Neighbours |
| --- | --- | --- |
| K, agreeing lookbacks | 2 | 1, 3 |
| Chandelier ATR multiple | 3.0 | 2.5, 3.5 |
| Longest lookback | 120 | 90, 180 |

## Expected sign

- t-statistic and DSR: higher than a single 20-bar breakout, from fewer false entries.
- Trade count: lower than v2's; it must still reach 100 across Train + Validation.
- Bear stretch: from FAIL toward a positive or flat return.

## Falsification test

Rejected if, on Train, the bear-stretch check or the neighbours check fails. The full gate decides certification.

## Notes

- Inferred: daily returns will correlate highly with swing-b1-trend-slope. The batch's tie-break uses correlation.
