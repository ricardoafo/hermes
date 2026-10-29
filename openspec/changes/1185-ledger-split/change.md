# Change: 1185-ledger-split

**SDD change**: `1185-ledger-split`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1185-ledger-split/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`adapters/ledger/sqlite_ledger.py` was 2,023 lines: one `SqliteLedger`
class covering eight tables, plus the row codecs the read-only readers
import. The class becomes a facade over five per-table store modules on
one shared core. The codecs move to a public `codecs.py`. Every method
body moves unchanged, and there is no schema or behaviour change. The one
new seam is an optional clock for the `recorded_at` stamps (finding A-15).

## Scope

### In

- `_ledger_core.py`: the connection, the clock, the one-transaction
  `_write` and the event insert.
- `_projection.py`: the lot and consumption projection that trade and seed
  writes share.
- `_trades.py`, `_seeds.py`, `_marks.py`, `_bars.py`, `_events.py`: the
  public methods, grouped by table.
- `codecs.py`: the row and payload codecs, now public names. The parity,
  reconciliation and live-bar readers import them from here.
- `SqliteLedger(connection, *, clock=None)`: the stamps read the injected
  `ClockPort`, and the wall clock when none is passed.

### Out

- Wiring a clock at the callers: the six construction sites keep the wall
  clock.
- The file-size and complexity budgets themselves (issue #1201, merged).

## Decisions

### D-1: Mixins on a shared core, not delegation

- **Decision**: each store is a class over `_LedgerCore`, and
  `SqliteLedger` inherits all five.
- **Alternatives**: a facade that holds five store objects and forwards
  ~50 methods (every method written twice); one store per module with no
  shared base (the `_write` boundary would be copied five times).
- **Rationale**: the method bodies move byte for byte, so the review is a
  move, and invariant I-06 (ADR-003 §00) keeps its one `_write`.
  `LedgerPort` is structural, so the port does not change.
- **Cost of reversal**: LOW.

### D-2: One shared projection base

- **Decision**: trade and seed writes both rewrite the FIFO projection, so
  `_projection.py` is the base of `_trades.py` and `_seeds.py`.
- **Alternatives**: the seed store inheriting the trade store (it would
  look like the seeds depend on trade queries).
- **Rationale**: the dependency is on the projection only.
- **Cost of reversal**: LOW.

### D-3: `_events.py` instead of `_signals.py`

- **Decision**: the event-log reads (signals, notifications, risk halts,
  anomalies, trades and fill quality) live together.
- **Rationale**: they all read the one `events` table.
- **Cost of reversal**: LOW.

## Requirements

### R-1: No behaviour change

- **A-1.1**: `uv run pytest tests/unit/adapters/ledger tests/integration`
  passes, with no existing assertion changed.

### R-2: Every module within the size budget

- **A-2.1**: `uv run python scripts/dev/ratchets.py size` exits 0 with the
  `sqlite_ledger.py` baseline line removed.

### R-3: Stamps come from the injected clock

- **A-3.1**: `tests/unit/adapters/ledger/test_sqlite_ledger_clock.py`
  passes. It fails on main with `unexpected keyword argument 'clock'`.

## Design

```text
adapters/ledger/
  _ledger_core.py   _LedgerCore: __init__(conn, *, clock), _write, _insert_event
  _projection.py    _LotProjection(_LedgerCore): _reproject*, lot inserts
  _trades.py        _TradeStore(_LotProjection)
  _seeds.py         _SeedStore(_LotProjection)
  _marks.py         _MarkStore(_LedgerCore)
  _bars.py          _BarStore(_LedgerCore)
  _events.py        _EventStore(_LedgerCore)
  codecs.py         iso_utc, event_to_json, trade_from_payload, ...
  sqlite_ledger.py  SqliteLedger(_TradeStore, _SeedStore, _MarkStore, _BarStore, _EventStore)
```

## Test plan

- The existing ledger suite (640 tests), the reader tests and the
  older-schema scenario cover R-1.
- `test_codecs.py`: round-trip properties for `iso_utc` and
  `decimal_text`, the naive-datetime refusal, and a trade payload
  round-trip.
- `test_sqlite_ledger_clock.py`: one stamp test per store module that
  writes one (events, bars, marks).

## Risks

- The private codec names (`_iso_utc` and the others) are gone. Only the
  three readers and two test files imported them, and all five are updated.
