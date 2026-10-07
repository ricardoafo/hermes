# Change: 797-shadow-day-zero

**SDD change**: `797-shadow-day-zero`
**Date**: 2026-10-06
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/797-shadow-day-zero/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-021 makes the shadow run since 2026-10-04 a warm-up and starts v1's
counted 90 days at day 0, once every Ready rule holds. The Code Owner closes
the warm-up on the VPS. This change adds `hermes shadow-start`: it checks the
Ready rules a program can check and lists the rest for the Code Owner. On
`--confirm START`, with serve stopped, it archives the ledger read-only,
removes it so serve starts fresh, and writes the day-0 record that Ready
rule 5 asks for. The main risk is losing the warm-up ledger, so the copy is
verified (integrity check, equal row counts) before the original is removed.

## Scope

### In

- `application/strategy_protocol/day_zero.py`: Ready checks for rules 1, 2
  and 7, the manual rules 3, 4 and 6, the tree hash, and the day-0 record.
- `entrypoints/shadow_start.py` + `commands/shadow_start.py`: the command.
- `docs/guides/vps-operations.md`: the day-0 procedure.

### Out

- The shadow trade count in `hermes certify` at the end of shadow, flagged
  weak under 30 round trips (decision 10). This is a separate change to the
  certify shadow gate.
- A run id inside serve's ledger rows: the fresh ledger is the run, and the
  console's run-aware work (issue #731, issue #718) reads the record.
- Machine checks for rules 3, 4 and 6: rule 4 can become one once issue #796
  lands.

## Decisions

### D-1: Program checks what it can, the Code Owner confirms the rest

- **Decision**: rules 1, 2 and 7 are checked. Rules 3 (defects), 4 (serve
  kill and drift, issue #796) and 6 (parity) print as `confirm by hand`, and
  `--confirm START` is the Code Owner's word on them.
- **Alternatives**: block on rules no program can decide (the command could
  never run); skip them silently (hides that a person confirmed them).
- **Rationale**: ADR-021 §2 lists the rules; day 0 is the Code Owner's call.
- **Cost of reversal**: LOW.

### D-2: Fresh run = fresh ledger file

- **Decision**: the warm-up ledger is copied with SQLite's backup API, checked
  (`integrity_check`, the same row count per table), made read-only
  (file 0444, directory 0555), then removed with its `-wal` and `-shm`. Serve
  applies every migration to a new ledger on start.
- **Alternatives**: rename the file (no verified copy, and a crash between
  steps leaves nothing at the ledger path); add a run id column to every
  table (a schema change with no reader yet).
- **Rationale**: issue #797 asks for an empty ledger and a read-only archive.
- **Cost of reversal**: LOW: the archive can be copied back.

### D-3: Refuse while serve runs

- **Decision**: `systemctl is-active --quiet hermes.service` must report
  inactive before anything changes.
- **Rationale**: copying a ledger serve is writing to can lose its last rows;
  issue #855 notes that serve has no single-instance lock yet.

### D-4: Record next to the ledger, never overwritten

- **Decision**: `runs/<strategy>-<UTC stamp>.json`, created with `O_EXCL`
  and mode 0444, holding the engine version, the release, the config tree
  hash, both pre-registration hashes, the data manifest hash, the parameter
  fingerprint, the archive path and hash, and every check.
- **Rationale**: Ready rule 5; the record must outlive the run it starts.

## Requirements

### R-1: Ready checks

- **A-1.1**: `uv run pytest tests/unit/application/strategy_protocol/test_day_zero.py`

### R-2: Dry run changes nothing

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_shadow_start.py -k dry_run`

### R-3: Refusals

- **A-3.1**: `uv run pytest tests/unit/entrypoints/test_shadow_start.py -k refused`

### R-4: Archive and fresh ledger

- **A-4.1**: `uv run pytest tests/unit/entrypoints/test_shadow_start.py -k "archive or removed"`

### R-5: Record

- **A-5.1**: `uv run pytest tests/unit/entrypoints/test_shadow_start.py -k record`

## Design

```text
hermes shadow-start <sid> [--db] [--config-dir] [--archive-dir] [--confirm START]
  checks: rule 1 notebook_check, rule 2 certification_check,
          rules 3/4/6 MANUAL_RULES, rule 7 roster_check
  any FAIL → exit 1; service active → exit 1; no START → dry run, exit 0
  START → _archive (backup, integrity, row counts, chmod) → record (O_EXCL)
        → remove ledger, -wal, -shm → print notebook entry
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green.

Test tiers touched: unit

## Risks

- **Wrong ledger path**: `--db` defaults to `./hermes.sqlite3` like every
  command. The runbook passes `/var/lib/hermes/hermes.sqlite3` explicitly,
  and the dry run prints the path before anything changes.
- **Certification store location**: rule 2 reads `certification/` next to
  `--db`, the same place `hermes certify` writes when run from
  `/var/lib/hermes`.

## Dependencies on other changes

- Rule 2 needs the backtest gate (issue #735, after issue #822).
- Rule 4 is confirmed by hand until issue #796 lands.
