# Change: 538-ledger-verify

**SDD change**: `538-ledger-verify`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/538-ledger-verify/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The VPS restart runbook expects `hermes ledger verify` to print an OK line; the
command did not exist and `hermes migrate --status` covers only the schema level.
`hermes ledger verify [--db PATH] [--json]` opens the ledger with SQLite
`mode=ro`, runs `PRAGMA integrity_check`, and reports the migration level, the
event count and gaps in the event id sequence. Main risk: a wrong definition of
"gap" producing false alarms; D-1 fixes it against the real schema.

## Scope

### In

- `adapters/ledger/integrity.py`: `open_read_only`, `check_ledger`,
  `find_id_gaps` (pure), `LedgerReport`, `IdGap`.
- `entrypoints/ledger.py` and `entrypoints/commands/ledger.py`.

### Out

- Verifying event payloads, hash chains or cross-table consistency (lots vs trades).
- Any repair action; the command never writes.

## Decisions

### D-1: a gap is a missing `events.id` in `1..high-water`

`events.id` is `INTEGER PRIMARY KEY AUTOINCREMENT` and the table is append-only,
so ids are issued contiguously from 1 and a missing id means a deleted row. The
high-water mark is the larger of `MAX(id)` and `sqlite_sequence.seq` for
`events`, which also catches deletion of the newest rows. A rolled-back insert
does not consume an AUTOINCREMENT id, so it is not a false positive. Gaps are
reported as runs (`7-9`). No `occurred_at` or `correlation_id` ordering is
checked: those are not sequences.

### D-2: schema level is the version prefix of the latest applied migration

The level is the version prefix of the highest applied migration file, kept as
text because migration names moved from `NNNN` to timestamps such as
`20261004T0300`; it is `0` when none is applied. It reuses `pending_migrations` from the migration runner (the same
source as `hermes migrate --status`). That function creates its bookkeeping table
if absent, which a read-only connection cannot do, so when `schema_migrations`
does not exist every migration is treated as pending without calling it.

### D-3: pending migrations and a missing events table are findings

A ledger behind the code's migrations, or lacking `events`, is not what the
runbook means by OK, so both exit 1.

### D-4: unreadable beats finding

A file that is not SQLite, or that cannot be opened, exits 3 with the SQLite
message; findings (exit 1) are only for a ledger that could be read.

### D-5: text output

Success is `OK — N events, 0 gaps, schema vX` (the issue's wording; the runbook's
`0 mismatches` is not produced because the command checks no mismatches). Findings
print `FINDINGS — ...` then one bulleted line per finding.

## Requirements

### R-1: the ledger is opened with `mode=ro` and never written

### R-2: `PRAGMA integrity_check` rows other than a lone `ok` are findings

### R-3: missing event ids in `1..high-water` are findings, as runs

### R-4: the schema level and pending migrations are reported

### R-5: exit 0 OK, 1 findings, 2 usage (including no subcommand), 3 missing or unreadable

### R-6: `--json` emits one document with the same facts

## Design

`check_ledger(conn)` is pure over a connection: it runs the pragma, reads the
schema state, streams event ids and hands them to `find_id_gaps`. The entrypoint
opens the file read-only, renders text or JSON and maps results to exit codes.

## Test plan

`tests/unit/adapters/ledger/test_integrity.py` (ledgers built through the real
migrations; middle, leading and trailing gaps; pending migration; unmigrated file
unchanged on disk; read-only refusal; hypothesis property on `find_id_gaps`) and
`tests/unit/entrypoints/test_ledger.py` (every exit code, text and JSON output,
registry dispatch).

## Risks

- `integrity_check` scans the whole file; on a large ledger it takes seconds and
  holds a read transaction. Acceptable for an operator command.

## Dependencies on other changes

- #457 (command registry), #137 (migration runner).
