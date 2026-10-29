# Change: 1240-round-trip-cap

**SDD change**: `1240-round-trip-cap`
**Date**: 2026-10-20
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1240-round-trip-cap/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-029 §4 caps each candidate at 250 round trips in any rolling 90-day window, half of ADR-002's 500 account-wide
line, and makes the cap a gate row in the candidate's pre-registration, checked by the generic gate. This change adds
that row: every batch candidate signs `round_trips_90d_max` (at most 250), the funnel evidence records when each
round trip opened, and the generic gate adds a `round_trip_cap` PASS/FAIL row after the funnel rows. The two batches
signed before the row existed, `swing-b1` and `swing-b2`, are exempt by batch id and parse, check and judge exactly as
before. An optional `holding: under_a_day` (ADR-029 §5) also needs the cap, in any batch.

## Scope

### In

- `src/hermes/application/calibration/round_trip_cap.py` (new): the busiest closed 90-day window and the gate row.
- `src/hermes/application/calibration/batch_floors.py` (new): the bounds a batch may tighten but never loosen.
  `Floor` and `FLOORS` move here unchanged from `research_batch.py` (which re-exports them), so that module stays
  under its size-ratchet baseline; `signed_cap` reads the candidate keys.
- `research_batch.py`: candidate keys `round_trips_90d_max` (required outside `SIGNED_BEFORE_CAP_ROW`) and
  `holding` (optional), validated on the `funnel_floors` check; `Candidate.round_trips_90d_max`.
- `backtest_funnel.py` / `evidence_files.py`: optional `round_trip_opened_at` in `funnel-evidence.json`.
- `signed_gate.py` and `entrypoints/certify.py`: the row for a candidate that signs a cap.
- `application/backtest/performance.py`, `scripts/backtest/funnel_evidence.py`, `run_portfolio_backtest.py`: write
  the opening times from the same round-trip loop as `trade_count`.
- `docs/experiments/templates/research-batch.md` (unsigned template): both keys on every candidate.
- Test fixtures (`tests/support/research_batch.py`, `candidate_evidence.py`): every synthetic candidate signs 250
  and its evidence carries round-trip times, as a batch signed from now on must.

### Out

- Sweeps (ADR-031 §6): a sweep family's candidates are built by `research_sweep.py`, owned by the Backtesting lane;
  `Candidate.round_trips_90d_max` defaults to `None`, so sweeps are unchanged. Wiring the cap to `1h` families waits
  for issues #1237 and #1238.
- The console Lab screen's fixed list of 17 checks (`application/console/lab.py`) does not list the new row yet.
- The interface contract (`research-funnel/interface.md`) is the Backtesting thread's to bump; §1, §2 and §3 gain
  the keys below.

## Decisions

### D-1: every candidate signs the cap, never above 250

- **Decision**: `round_trips_90d_max: <int>` on every batch candidate; 1 to 250, refused above 250 as looser than
  ADR-029 §4 (the way `FLOORS` refuses a loose funnel threshold), and refused when missing, on `funnel_floors`.
- **Rationale**: ADR-029 §4 applies the cap to every candidate's Train run, not only to those holding under a day
  (§5 is the under-a-day cost card), and ADR-026 §1 has thresholds come only from the batch.
- **Alternatives**: a constant 250 the gate applies without a batch key (the row would not be in the candidate's
  pre-registration, as §4 says); a key in the shared `backtest_funnel` block (the ADR puts it per candidate).
- **Cost of reversal**: LOW.

### D-2: a round trip sits in the window that holds its opening fill

- **Decision**: the window key is `ClosedRoundTrip.entered_at`, the time the shared round-trip loop already records.
- **Rationale**: one loop then counts both `trade_count` and the times (issue #1179), so the cap row and the trade
  rows cannot count different trades; the shadow parity check keys on `entered_at` too. A closing time would need a
  new field on the domain type and new expected values in the existing round-trip tests. The two readings differ only
  for a trip that straddles a window edge.
- **Cost of reversal**: LOW (one field and the evidence key).

### D-3: windows are closed at both ends; only Train trips count

- **Decision**: trips opened exactly 90 days apart share a window (the ADR-002 tax guard's closed interval); only
  trips opened on or before `train_end` count, on a Train and on a Train + Validation run.
- **Rationale**: ADR-029 §4 reads "A candidate's Train run fails its gate"; counting Validation trips too would be a
  stricter rule than the one signed.
- **Cost of reversal**: LOW.

### D-4: the batches signed before the row are exempt by batch id

- **Decision**: `SIGNED_BEFORE_CAP_ROW = frozenset({"swing-b1", "swing-b2"})` in `batch_floors.py`, read against the
  `batch_id` the parser already uses. Their candidates may sign no cap and get no `round_trip_cap` row; every other
  batch must sign it. `holding` stays optional, and `holding: under_a_day` without a cap is refused in any batch.
- **How signed batches stay valid**: no signed file is edited. On this branch `swing-b1` and `swing-b2` parse to the
  same objects as on `b6b79442`, print byte-identical `hermes batch check --require-signed` output, and judge with
  the same rows. Tests pin the exemption list, that both carry no cap, and that a new batch without the cap is
  refused under `funnel_floors`.
- **Alternatives**: optional cap with a required `holding` (left the cap avoidable for multi-day candidates, which
  §4 also covers); exempting by `content_sha256` (the batch id is unique per experiment and immutable once signed,
  and is what the operator reads).
- **Cost of reversal**: LOW.

### D-5: missing evidence fails the row

- **Decision**: a capped candidate whose `funnel-evidence.json` has no `round_trip_opened_at` fails the row
  ("not computed"), as every funnel check fails closed.

### D-6: the check is named `round_trip_cap`

- **Decision**: one lowercase token without digits, like every other check name, so the job summary and the stdout
  filters that keep `PASS|FAIL <id> <check>` read it unchanged.

## Requirements

### R-1: the row fails above the cap and passes at it

- **A-1.1**: `uv run pytest -q tests/unit/application/calibration/test_round_trip_cap.py` exits 0 (250 pass, 251
  fail, exactly 90 days vs 90 days + 1 s, brute-force and monotonic properties).

### R-2: the gate adds the row only for a capped candidate

- **A-2.1**: `uv run pytest -q tests/unit/application/calibration/test_signed_gate.py -k round_trip` exits 0.

### R-3: the batch format refuses a missing or loose cap

- **A-3.1**: `uv run pytest -q tests/unit/application/calibration/test_research_batch.py -k cap` exits 0.
- **A-3.2**: `uv run pytest -q tests/unit/application/calibration/test_batch_floors.py` exits 0.

### R-4: signed batches are unchanged

- **A-4.1**: `uv run hermes batch check docs/experiments/swing-mvp1/batches/swing-b1.md --require-signed` prints the
  same lines as on `b6b79442` (likewise `swing-b2`).

### R-5: the evidence carries the times and refuses bad ones

- **A-5.1**: `uv run pytest -q tests/unit/application/calibration/test_evidence_files.py
  tests/unit/scripts_backtest/test_funnel_evidence.py` exits 0.

## Design

Batch candidate (the cap required outside `SIGNED_BEFORE_CAP_ROW`, `holding` optional):

```yaml
  - strategy_id: swing-b3-hourly
    ...
    holding: under_a_day          # optional; or a_day_or_more (ADR-029 §5)
    round_trips_90d_max: 250      # ADR-029 §4: 1..250, every candidate
```

Funnel evidence (optional): `"round_trip_opened_at": ["2021-01-01T04:00:00+00:00", ...]`, one UTC ISO 8601 time per
closed round trip of the base run; its length must equal `trade_count`, and none may be on or after `oos_start`.

Gate: `evaluate_signed_gate(..., round_trip_cap: int | None = None)`; with a cap it appends
`GateCheck("round_trip_cap", "<= {cap} round trips in any 90 days of Train", "<busiest>", busiest <= cap)` after
the funnel rows. The verdict record lists it as `{"check": "round_trip_cap", "status": "PASS|FAIL"}` with no value.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. The red run: `test_round_trip_cap.py` failed on collection before the module existed.

Test tiers touched: unit, property

## Risks

- **Evidence from runs before this change has no times.** A capped candidate judged on old evidence fails the row
  closed; the exempt batches sign no cap, so no existing run is affected.
- **The evidence digest changes for new runs** because of the new key; no verdict reads the digest (as for `folds`).
- **A draft batch reusing an exempt id** would also be exempt; batch ids are unique per experiment and a signed
  batch is immutable, so a new batch cannot take `swing-b1` or `swing-b2` without replacing a signed file.

## Dependencies on other changes

- ADR-029 (proposed, tracking issue #1105). ADR-031 §6 names this cap for `1h` sweep families.
