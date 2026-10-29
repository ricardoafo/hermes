# Change: 1178-ledger-reader-base

**SDD change**: `1178-ledger-reader-base`
**Date**: 2026-10-18
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1178-ledger-reader-base/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Seven read-only ledger readers each repeated the same lifecycle: resolve
the path, refuse a missing file, open with `mode=ro`, turn `sqlite3.Error`
into `LedgerError`, then `close`, `__enter__` and `__exit__`. The console
read model had an eighth copy of the open step. `ReadOnlyLedgerReader` and
`open_read_only` in `adapters/ledger/connection.py` now hold the one copy;
each reader keeps only its queries.

## Scope

### In

- `ReadOnlyLedgerReader` (open, close, `with`) and `open_read_only`.
- The parity, filing, reconciliation, status, trade-rates, anomaly and
  console activity readers subclass it; `connect_read_only` calls
  `open_read_only`.
- A contract test over every shipped schema and a lifecycle guard.

### Out

- Moving row decoding into a shared codec module. The stored-cell codecs
  already live once in `adapters/ledger/_cells.py` (issue #842); the
  remaining raw `Decimal(str(...))` reads in the console read model and
  `SqliteLedger` change error behaviour and belong with that work.
- The repo-wide duplicate-block ratchet (issue #1201).
- Entrypoints that open a raw read-only connection for one query
  (`version`, `risk_inputs`, `tui_tax`).

## Decisions

### D-1: A base class, not a context-manager function

- **Decision**: readers inherit `ReadOnlyLedgerReader`.
- **Alternatives**: a `reading(path)` context manager passed to plain
  functions (would change every reader's public shape and its callers).
- **Rationale**: every caller keeps `Reader.open(path)` and `with` as today.
- **Cost of reversal**: LOW.

### D-2: `any_thread` on every reader

- **Decision**: the base `open` takes `any_thread`, which only the status
  reader used before.
- **Alternatives**: keep it on the status reader alone (a second `open`).
- **Rationale**: one signature; the default keeps today's behaviour.
- **Cost of reversal**: LOW.

## Requirements

### R-1: One lifecycle

Only `ReadOnlyLedgerReader` defines the read-only `open`/`close` pair under
`hermes.adapters`.

### R-2: Same errors

A missing file is `FileNotFoundError` naming the path; SQLite refusing the
open is `LedgerError("cannot open <path> read-only: ...")`; the connection
refuses writes.

### R-3: Every schema answers or raises `LedgerError`

Every query of every reader, over an empty file and over a ledger at each
shipped migration, returns or raises `LedgerError`, never a raw
`sqlite3.Error`.

## Design

`open_read_only(path, *, any_thread)` wraps `open_ledger(read_only=True)`
and converts `sqlite3.Error`. `ReadOnlyLedgerReader.__init__` stores the
connection as `self._conn`, which every reader's queries already use, so
query code is untouched.

## Test plan

- `tests/unit/adapters/ledger/test_connection.py`: R-2, `with` closes,
  and `any_thread`.
- `tests/unit/adapters/ledger/test_readers_over_every_schema.py`: R-3 for
  24 queries across 11 schemas (an empty file and each of the 10 shipped migrations), and the R-1 scan.

## Risks

- A reader that overrode one lifecycle method would lose it. None did:
  the seven copies were identical apart from the status reader's
  `any_thread`.
