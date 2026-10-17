# Change: 796-shadow-checks-in-serve

**SDD change**: `796-shadow-checks-in-serve`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/796-shadow-checks-in-serve/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-021 Ready rule 4: the pre-registration §10 shadow kills and the §7 drift check run inside
`hermes serve` on their own. The code for the Sharpe streak existed (`ShadowSharpeKill`) but
nothing called it, and nothing counted SA-9 fires or drift. This change runs all three once per
completed UTC day from serve's own ledger, records what fires through the `RiskHaltRecorded` event
of #749 (strategy-wide rows, no pair), alerts it, and holds a kill: no new entries from then on,
exits still fill, across restarts. The certification guardrails turn a shadow kill into DEAD and a
shadow drift breach into UNTRUSTED. No threshold changes; every output is a rule id.

## Scope

### In

- `application/strategy_protocol/shadow_checks.py`: daily equity from portfolio marks, the SA-9
  repeat kill, the Sharpe streak over the full history, the 7-day drift p50.
- `KillTriggers`: the signed §10 values the checks read (30 bps, 7 days, 3 fires, 30 days).
- `ExperimentState.killed_by` and `ShadowExecutor.hold_kill`: a held kill fires on every entry.
- `halt_record.shadow_check_records`; `HaltKind.DRIFT`; `RiskHaltRecorded.pair` may be `None`.
- `SqliteLedger.risk_halts`; `has_risk_halt` matches a pairless row.
- Serve: the check on the first bar of each UTC day, before the strategy; the held kill at boot.
- `apply_guardrails`: shadow kill → DEAD, drift breach in shadow → UNTRUSTED.
- Notes and `hermes status` read a pairless row as "all pairs".

### Out

- M-09 and M-10. They are Done rules (ADR-021 Done 3), need a replay of the whole shadow window,
  and read at most ~20 trades in v1's 90 days; they belong with `hermes certify` at the end of the
  window. Tracked in a follow-up issue.
- `hermes certify` reading the kill and drift rows from the ledger (issue #797); until then the
  operator's evidence file carries `kill_fired` and `drift_breached`.
- The live-phase kill inputs (issue #514).

## Decisions

- **D-1 Daily, from the ledger, recomputed.** Each check reads the whole history again (marks,
  halts, fills). A restart cannot lose a streak, and nothing new is stored but the rows that fire.
- **D-2 When.** The first bar closing on a new UTC day checks the day before it, before the
  strategy sees that bar, so a kill found for the day stops that bar's entry.
- **D-3 Daily equity.** The last portfolio mark of each UTC day. A day without a mark breaks the
  Sharpe streak, as `ShadowSharpeKill` already rules.
- **D-4 SA-9 repeat.** HALT rows of one `RO-xx` rule with `end − 30 d ≤ occurred_at < end`; 3 or
  more of one rule kill. Kill rows and unclassified halts do not count.
- **D-5 Drift (Code Owner card, 2026-10-17).** The median of |drift_bps| of the fills in the last 7
  days above 30 bps is a breach. Direction is ignored ("divergence"); a window without a measured
  fill never breaches. No baseline is subtracted: none is measured, and the signed text and the
  certification gate both use 30 bps flat.
- **D-5b Which fills (Code Owner card, 2026-10-17: "v1 entries only").** v1's §6.2 model prices a
  stop exit at stop × 0.9985 while serve sells at the 4h close, so v1 exits drift 70 to 390 bps by
  construction (issue #785's gap). `KillTriggers.shadow_drift_counts_exits` is `False` for v1: its
  exits stay out of the median and serve logs how many were left out. A pre-registration that models
  exits at a market price (the v2 draft: close × 0.9995) sets it `True` and counts every fill.
- **D-6 Kill vs drift.** Following ADR-021 (the later ADR wins): a kill is DEAD and stops entries;
  a drift breach sends the strategy back to UNTRUSTED and does not stop trading.
- **D-7 Rows.** Strategy-wide rows have no pair and are stamped at the end of the checked day, so
  a re-check finds them (`has_risk_halt`). A held kill is recorded once; per-entry refusals under it
  are recorded as kills of the pair, as in #749.

## Requirements

- R-1 Three RO-04 halts in 30 days kill at the next day's first bar; that bar's entry is refused.
- R-2 60 consecutive days with the 30-day Sharpe below 0.3 kill; 59 do not.
- R-3 A 7-day drift median above 30 bps breaches; exactly 30, no fills or older fills do not.
- R-4 A recorded kill holds after a restart and is not recorded again.
- R-5 Exits still fill under a held kill.
- R-6 A shadow kill moves BACKTEST_CERTIFIED and SHADOW_CERTIFIED to DEAD; a drift breach to
  UNTRUSTED; later states are not moved by shadow evidence.
- R-7 No value reaches a row, an alert or `hermes status`.

## Design

```text
_BarLoop.handle(bar)
  tracker.mark(bar); executor.observe(bar)
  _shadow_checks(bar)            # once per new UTC day
    run_shadow_checks(day, equity=daily_equity(marks), halts, fills, triggers)
    kill → executor.hold_kill(rule)  (ExperimentState.killed_by → KillDecision first)
    shadow_check_records(...) → _record_halt (dedup, append, alert)
  strategy.on_bar(...)
build_composition: _held_shadow_kill(ledger) → executor.hold_kill
```

## Test plan

- Unit: `test_shadow_checks.py` (R-1..R-3, property: direction ignored), `test_halt_record.py`,
  `test_kill_trigger_evaluator.py`, `test_shadow.py` (R-5), `test_risk_halt_rows.py`,
  `test_certification_gates.py` (R-6), `test_status.py`, template snapshots.
- Integration: `test_serve_fills.py` (R-1, R-4, SEV-1).

## Risks

- Deploying this to the warm-up run on the VPS can kill it (SA-9 repeat). That is the signed rule;
  the warm-up run is retired at day 0 anyway (ADR-021 §3).
- A read of the whole history once a day grows with the ledger; a year is about 8,000 marks.
