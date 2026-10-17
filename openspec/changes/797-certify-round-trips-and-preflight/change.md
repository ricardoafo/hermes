# Change: 797-certify-round-trips-and-preflight

**SDD change**: `797-certify-round-trips-and-preflight`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/797-certify-round-trips-and-preflight/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The last two items of issue #797 after `hermes shadow-start` (PR #888,
`openspec/changes/797-shadow-day-zero/change.md`, frozen). First, ADR-021 §3 has `hermes certify`
report the shadow trade count at the end of shadow and flag fewer than 30 round trips as a weak
execution check, with no rule change. Second, the issue asks for tests as the service user with
restrictive permissions. Run that way, `shadow-start` turned out to archive the ledger and write the
day-0 record, then fail to remove a ledger in a read-only directory. Serve would then resume the
warm-up ledger under a day-0 record. The fix is a write probe of every directory before any change.

## Scope

### In

- `SqliteStatusReader.round_trips`: SELL `TradeExecuted` rows of one strategy.
- `hermes certify --db PATH` (default: serve's ledger): toward SHADOW_CERTIFIED, prints
  `WEAK` under 30 round trips, `NOTE` otherwise, or a note that no ledger was found.
- `shadow-start`: `_check_writable` for the ledger, archive and run-record directories.
- Tests that run as a non-root account with read-only directories, and one that holds as root.

### Out

- Reading the kill and drift rows into the shadow evidence (after issue #796 merges).
- M-09 and M-10 (issue #965).

## Decisions

- **D-1 Round trip = SELL fill.** Every exit closes the whole position (v1 has no partial exits),
  so the SELL count is the round-trip count.
- **D-2 Flag only.** The count is printed beside the gate rows and changes no check (ADR-021 §3).
- **D-3 Probe, not mode bits.** A real temporary file in each directory (or its nearest existing
  parent) also catches a read-only mount, an unowned directory and a path taken by a file.

## Requirements

- R-1 Under 30 round trips toward SHADOW_CERTIFIED prints WEAK; 30 prints NOTE.
- R-2 The verdict is the same whatever the count.
- R-3 Other gates print no count; another strategy's trades are not counted.
- R-4 A ledger, archive or run-record directory the start cannot write: exit 3, nothing changed.

## Design

```text
certify.main --db → _round_trips → SqliteStatusReader.round_trips → run(round_trips=)
  run: report.to_state == SHADOW_CERTIFIED → _print_round_trips
shadow_start.run (confirmed) → _check_writable(db dir, archive dir, runs dir) → archive → record → remove
```

## Test plan

- `tests/unit/entrypoints/test_certify.py::TestShadowRoundTrips` (R-1..R-3).
- `tests/unit/entrypoints/test_shadow_start.py::TestUnwritableDirectories` (R-4). The two
  permission tests skip as root, which ignores directory permissions. Run as `nobody` they fail
  without the fix and pass with it; the run-records test fails without the fix even as root.

## Risks

- `hermes certify` now reads the ledger when one is at its path; it opens it read-only.
