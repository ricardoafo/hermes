# Change: 1021-v2-shadow-gate

**SDD change**: `1021-v2-shadow-gate`
**Date**: 2026-10-17
**Status**: Proposed
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1021-v2-shadow-gate/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes certify swing-mvp1-v2` evaluates every gate past the backtest. The
shadow gate applies pre-registration v2 §7 as signed: shadow ends at 90 days
and 30 round trips, whichever is later, capped at 180 days, and reaching the
cap first counts as a fail. The three numbers are read from the signed v2
file's §7 text. The OOS, live-approval and live gates are v1's, which v2 keeps
unchanged (§4, §7, §9 Primary, §10 "Shadow phase and live phase"); the parser
refuses the file if one of those stops saying "Unchanged from v1". v2's
fingerprint gains `exit_pricing` whenever it differs from the signed bar-close
exit, so every record made so far keeps its fingerprint. Main risk: a v2 shadow
certified on days alone, without its 30 round trips, so a missing count fails
the gate.

## Scope

### In

- `application/calibration/certification_gates_v2.py`: `V2ShadowRules`,
  `parse_v2_shadow_rules`, the shadow rows in `evaluate_v2_gate`, the later
  gates through v1's, and `v2_parameter_fingerprint`.
- `application/calibration/certification_gates.py`: `ShadowEvidence.round_trips`
  (optional; v1's gate does not read it).
- `entrypoints/certify.py`: parse `shadow.round_trips` from the evidence
  file, parse the v2 shadow rules, fingerprint v2 with its exit pricing.
- Unit and property tests; `changelog.d/1021-added.md`.

### Out

- Counting round trips from the ledger into the evidence file: the operator
  submits the count, as with `shadow.days` (PR #981 prints the ledger count).
- The ADR-021 v2+ real-money rules beyond the pre-registration (one year
  beside the champion): not in pre-registration v2.

## Decisions

### D-1: The shadow numbers are read from the signed v2 file

- **Decision**: `parse_v2_shadow_rules` joins §7's lines and takes the one
  match of "ends at N days and M round trips, whichever is later, capped at
  K days". Zero or two matches refuse.
- **Alternatives**: constants mirroring the text (two copies of each number).
- **Rationale**: the same one-source rule as the DSR floor (#1008 D-1).
- **Cost of reversal**: LOW.

### D-2: `shadow.days` is the shadow's length when it ended

- **Decision**: the gate passes when days ≥ 90, round trips ≥ 30 and days ≤
  180. The evidence's `days` is the length of the shadow at its end, which
  is the later of day 90 and the 30th round trip, or the cap.
- **Alternatives**: a timestamp per round trip, so the gate finds the end
  itself (a new evidence shape for one row).
- **Rationale**: v1's `shadow.days` already means the shadow's length; the
  cap row then reads "reached the cap first" directly. A shadow that ran past
  180 days before its 30th round trip fails as inconclusive, as §7 says.
- **Cost of reversal**: LOW.

### D-3: The later gates are v1's, guarded by the "Unchanged from v1" text

- **Decision**: out of `SHADOW_CERTIFIED`, `OOS_CERTIFIED` and
  `LIVE_APPROVED`, the v2 gate is v1's gate on v1's `PreRegistration`. The
  parser refuses a v2 file whose §4, §7, §9 Primary or §10 "Shadow phase and
  live phase" no longer starts "Unchanged from v1".
- **Alternatives**: v2 copies of the OOS and live thresholds.
- **Rationale**: v2 §0: "Everything else (universe, windows, entry rules,
  sizing, caps, fees, OOS rules, roles) is unchanged from v1".
- **Cost of reversal**: LOW.

### D-4: `exit_pricing` enters the fingerprint only when it is not the signed one

- **Decision**: `v2_parameter_fingerprint` is v1's formula over the v2
  entry, plus an `exit_pricing` key when the entry's exit pricing is not
  `bar_close`.
- **Alternatives**: always add the key (every v2 record made by #1008 would
  revert to `UNTRUSTED` on its next certify).
- **Rationale**: an entry with another exit pricing gets another
  fingerprint, and a record certified on the signed exit keeps its
  fingerprint.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The v2 shadow boundary is exact

- **A-1.1**: 90 days and 30 round trips pass; 89 days, 29 round trips or 181
  days fail; a missing round-trip count fails
  (`tests/unit/application/calibration/test_certification_gates_v2.py`).

### R-2: v1 unchanged

- **A-2.1**: `tests/unit/application/calibration/test_certification_gates.py`
  and the v1 certify tests pass unedited.
- **A-2.2**: `v1_parameter_fingerprint` returns the same digest as before.

### R-3: Records stay valid

- **A-3.1**: `v2_parameter_fingerprint` with `bar_close` equals
  `v1_parameter_fingerprint` of the same entry; with `stop_order` it differs.

## Design

```text
evaluate_v2_gate(record, evidence, v1, rules, shadow, funnel)
  report = evaluate_next_gate(record, evidence, pre_reg=v1)   # every state
  UNTRUSTED          -> dsr row at >= rules.dsr_min, funnel rows appended
  BACKTEST_CERTIFIED -> shadow_days at >= shadow.min_days,
                        + shadow_round_trips >= shadow.min_round_trips
                        + shadow_cap: days <= shadow.max_days
  other states       -> v1's report as is
```

## Test plan

- Boundary tests for days, round trips and the cap; a property test that the
  shadow rows pass exactly when all three hold.
- Parser tests: the numbers from §7, zero or two matches refused, each
  "Unchanged from v1" guard refused when changed; one test on the signed file.
- Later gates: OOS before 2027 refused as in v1, live approval needs the typed
  phrase, live days as v1.
- Fingerprint: `bar_close` equals the v1 formula, `stop_order` differs.
- `hermes certify` reads `shadow.round_trips` from the evidence file.

## Risks

- A wrong `shadow.days` in the evidence file (for instance today's age rather
  than the shadow's length at its end) fails a shadow that met §7 early. It
  fails closed, and the operator re-runs with the correct length.
