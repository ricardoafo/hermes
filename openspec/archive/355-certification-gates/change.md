# Change: 355-certification-gates

**SDD change**: `355-certification-gates`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#355)

Mirror of engram topic key `sdd/355-certification-gates/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The certification lifecycle (pre-registration v1 §7) is enforced in code. Until now
`CertificationLifecycle.advance` only checked that one fill existed, and every later
transition was "operator-triggered" with no condition. This change adds one gate per
transition, reading every threshold from `PreRegistration` (§4, §7, §9, §10). It also
adds the D-8 guardrail: any change to v1's parameters sends the strategy back to
`UNTRUSTED`. A new `hermes certify <strategy_id>` command evaluates the next gate and
prints pass or fail per check with its evidence. `hermes serve` refuses
`allow_mainnet=True` unless the strategy is `LIVE_APPROVED` or `CERTIFIED` with an
unchanged parameter fingerprint. Statistics from #354 do not exist yet, so a missing
statistic fails its gate closed. Main risk: the OOS gate is one-shot, so a bug that
consumed it early would end the experiment. It is consumed only when complete OOS
evidence is evaluated on or after 2027-01-01 from `SHADOW_CERTIFIED`.

## Scope

### In

- `PreRegistration`: add the two §9 secondary rows that were missing
  (`profit_factor_min = 1.3`, `fees_to_gross_pnl_max = 0.30`) and a
  `certification_schedule()` accessor (2027-01-01, 90-day shadow, 30 bps p50 drift,
  90-day live).
- `H1AcceptanceGate` checks all nine §9 rows through the shared §9 evaluator.
- `application/calibration/certification_gates.py`: evidence types, the per-transition
  gates, the D-8 and drift-breach reverts, the one-shot OOS rule, the parameter
  fingerprint, and the live-approval check.
- `CertificationLifecycle.advance` requires a passed gate report for the transition.
- `ports/certification.py` and `adapters/certification/json_store.py`: one JSON record
  per strategy (state, fingerprint, OOS evaluation time, transition history).
- `domain/strategy/swing/adapter.py`: public `swing_parameters()` mapping of the §5
  constants, used for the fingerprint.
- `entrypoints/certify.py` and the `hermes certify` subcommand.
- `serve.check_startup`: mainnet requires live approval.

### Out

- Computing the statistics (DSR, block bootstrap, walk-forward, purged CV): #354.
- Data manifest and trial logging: #356, #357. The §6 fee and slippage model joins the
  fingerprint when #356 lands.
- Producing the evidence file from the harness and shadow ledger: follows #354.
- Writing lab-notebook entries on transition: `certify` prints the transition; the
  operator logs it per §12 until a follow-up automates it.
- The fill path (#313, #353).

## Decisions

### D-1: One gate per transition, thresholds from `PreRegistration` only

- **Decision**: Each forward transition has a pure gate function returning a
  `GateReport` with one `GateCheck` per rule (name, requirement, observed value, pass).
  Every number comes from `PreRegistration`.
- **Alternatives**: thresholds as module constants in the gate module (two sources of
  truth); parse the Markdown at runtime (fragile; the parser already mirrors the signed
  text as typed constants checked against the file).
- **Rationale**: #355 acceptance, "thresholds are read from one place". Tests pin each
  value.
- **Cost of reversal**: LOW.

### D-2: `BACKTEST_CERTIFIED` applies every §9 row on Train+Val

- **Decision**: §7 says "Backtest metrics (§9) pass on Train+Val", and §10 adds the
  backtest kill rules (DSR < 0.7, any secondary fails, drawdown ≥ 15%). The gate applies
  all nine §9 rows, primary and secondary, to the Train+Val metrics, plus at least one
  calibration fill.
- **Alternatives**: secondary rows plus DSR only (the §10 reading).
- **Rationale**: the stricter reading of the two, so nothing that §10 kills can pass.
  **Code Owner's call** whether the trade-count and bootstrap rows should also apply on
  Train+Val.
- **Cost of reversal**: LOW.

### D-3: Missing statistics fail closed

- **Decision**: every metric in the evidence is optional. A missing value fails its check
  with the observation "not computed (#354)".
- **Rationale**: #354 does not exist yet; a gate must never pass on absent evidence.
- **Cost of reversal**: LOW.

### D-4: OOS is one-shot and date-locked

- **Decision**: the OOS gate refuses (without consuming anything) before 2027-01-01, from
  any state other than `SHADOW_CERTIFIED`, after a previous OOS evaluation, or when any
  OOS metric is missing. Otherwise it evaluates H1 once and records the evaluation time,
  pass or fail. A pass moves to `OOS_CERTIFIED`; a fail moves to `UNTRUSTED` (§1: H0 not
  rejected) and the recorded time blocks any re-run under this pre-registration.
- **Rationale**: §4 discipline gate and §7 "one-shot test"; incomplete evidence must not
  burn the only shot.
- **Cost of reversal**: MEDIUM (the record format carries the OOS time).

### D-5: D-8 guardrail through a parameter fingerprint

- **Decision**: the fingerprint is SHA-256 over canonical JSON of the §5 swing
  parameters, the §2 capital budget and the §3 universe. It is stored when a gate passes.
  Any mismatch later sends every state except `DEAD` back to `UNTRUSTED`; the OOS
  evaluation time is kept, so a changed strategy can never re-certify under v1.
- **Alternatives**: hash `config/strategies.yaml` (misses the §5 constants, which live in
  code).
- **Cost of reversal**: LOW.

### D-6: Live approval needs an explicit sign-off, and mainnet needs live approval

- **Decision**: `OOS_CERTIFIED → LIVE_APPROVED` requires `--sign-off <name>` and
  `--confirm LIVE_APPROVED` typed exactly. `serve.check_startup` raises when
  `allow_mainnet` is true unless the stored record is `LIVE_APPROVED` or `CERTIFIED` and
  its fingerprint matches the running parameters. No store means refusal.
- **Rationale**: #355 acceptance; ADR-014 decision 2. Today no mainnet order adapter
  exists (#29), so the composition root is the one place every future mainnet path
  passes through.
- **Cost of reversal**: LOW.

### D-7: Record in a JSON file per strategy behind a port

- **Decision**: `CertificationStorePort` with `load` and `save`; the adapter writes
  `<dir>/<strategy_id>.json` atomically (temp file then replace). Default directory is
  `certification/` next to the ledger file.
- **Alternatives**: a table in the SQLite ledger (heavier migration for a record that
  changes a handful of times a year).
- **Cost of reversal**: LOW.

### D-8: Kill outcomes

- **Decision**: a fired shadow kill (§10 shadow phase) fails the shadow gate. A fired
  live kill (§10 live phase) during `LIVE_APPROVED` moves the record to `DEAD`.
  `DEAD` stays absorbing.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Thresholds match the pre-registration exactly

- **A-1.1**: `uv run pytest tests/unit/application/strategy_protocol/test_preregistration.py`
  pins all nine §9 values and the four schedule values.

### R-2: Each gate enforces its §7 entry criteria

- **A-2.1**: `uv run pytest tests/unit/application/calibration/test_certification_gates.py`
  covers pass and fail per check, fail-closed on missing values, and the reverts.

### R-3: OOS is one-shot and date-locked

- **A-3.1**: tests show refusal before 2027-01-01, from any state but
  `SHADOW_CERTIFIED`, after a prior evaluation, and on incomplete evidence, with nothing
  consumed in each refusal.

### R-4: Mainnet needs live approval

- **A-4.1**: `uv run pytest tests/unit/entrypoints/test_serve.py -k mainnet` shows
  startup refused without a record, with a non-approved record, and with a changed
  fingerprint.

### R-5: `hermes certify` prints every check and persists only passed transitions

- **A-5.1**: `uv run pytest tests/unit/entrypoints/test_certify.py`.

## Design

```text
src/hermes/
  application/calibration/certification_gates.py   new: evidence, gates, record, fingerprint
  application/calibration/lifecycle.py             advance(..., gate=GateReport)
  application/strategy_protocol/preregistration.py H1Thresholds + CertificationSchedule
  application/strategy_protocol/h1_acceptance_gate.py  delegates to section9_checks
  ports/certification.py                           CertificationStorePort
  adapters/certification/json_store.py             JsonCertificationStore
  domain/strategy/swing/adapter.py                 swing_parameters()
  entrypoints/certify.py                           hermes certify
  entrypoints/cli.py                               dispatch "certify"
  entrypoints/serve.py                             check_startup(..., certification_store=)
```

`hermes certify <strategy_id> [--evidence FILE] [--store-dir DIR] [--config-dir DIR]
[--sign-off NAME --confirm LIVE_APPROVED]`. Exit codes: 0 gate passed and recorded,
1 gate failed or refused, 2 usage, 3 config, evidence or store error.

Evidence file (every key optional; absent means not computed):

```json
{
  "backtest": {"dsr": 0.81, "bootstrap_ci_lower": 0.12, "trade_count": 64,
               "max_drawdown": 0.11, "recovery_days": 40, "profit_factor": 1.5,
               "ulcer_index": 7.2, "fees_to_gross_pnl": 0.18, "win_rate": 0.41,
               "calibration_fill_count": 64},
  "shadow": {"days": 92, "drift_p50_bps": 12.5, "kill_fired": false,
             "drift_breached": false},
  "oos": {"dsr": 0.75, "...": "same keys as backtest"},
  "live": {"days": 95, "kill_fired": false}
}
```

## Test plan

1. Every acceptance criterion above passes.
2. Existing lifecycle tests updated for the new `gate` argument: transitions that used to
   pass on a report alone now need a passed gate report. This is the behaviour #355 asks
   to change, not a silenced failure.
3. `bash scripts/dev/pr-preflight.sh` passes.

## Risks

- **OOS consumed early by a bug**: refusals never write the record; tests cover each
  refusal path.
- **Fingerprint too narrow**: the §6 fee and slippage model is not in it until #356.
- **Evidence is operator-supplied** until #354 produces it; a hand-edited file could
  pass a gate. The record keeps the evidence hash so a later audit can compare it.
