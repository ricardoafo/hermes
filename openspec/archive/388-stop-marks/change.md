# Change: 388-stop-marks

**SDD change**: `388-stop-marks`
**Date**: 2026-10-05
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

## Summary

The swing strategy recomputes its chandelier stop on every 4h bar it
holds a position, but only the stop of the bar a signal fires on is
stored. This change records the stop the strategy evaluated on every
held bar in a new `stop_marks` ledger table, and hands the last stored
stop back to the strategy when `hermes serve` restores a position after
a restart. The console can then show the stop history (issue #388), and
the stop ratchet (issue #680) gets the previous stop it needs across a
restart. Record-only: the stop and every signal are unchanged
(pre-registration v1 §11). The main risk is a decision change by
accident, so a property test shows a restored stop never changes a
signal, and another shows each record equals the §5.2 stop computed
independently on the same bars.

## Scope

### In

- `StopRecord` and `StopMark` (`hermes.domain.strategy.stop_mark`).
- `SwingStrategyAdapter.last_stop_record`, set on the entry bar, each
  held 4h bar and the exit bar.
- `OpenPosition.last_stop` (nullable), passed to `seed()`.
- `StopHistoryPort`, `SqliteLedger.record_stop_mark` and
  `latest_stop_mark(strategy_id, pair, entry_time)`.
- Migration `20261005T1300_stop_marks.sql`.
- `hermes serve` writes one row per held 4h bar and restores the last
  stop with the position.

### Out

- The ratchet itself (issue #680). It changes only the adapter's stop
  rule and builds on this table.
- Console display of the stop history.
- Any journal field: the stop is a price, and the daily review and
  briefings read the journal (ADR-012 D-02).

## Decisions

### D-1: A new table, written with INSERT OR IGNORE

- **Decision**: `stop_marks`, keyed on (strategy, pair, bar close).
  One row per held bar; a replayed bar after a restart writes nothing.
- **Alternatives**: Extend `portfolio_marks` (one row per bar of every
  stream, flat or held; mixes portfolio and strategy state). A ledger
  event (changes the event stream the parity check replays).
- **Rationale**: Ledger changes during the shadow test must be
  record-only and nullable. A new table is additive, so an older
  release ignores it and a rollback stays safe (issue #655).
- **Cost of reversal**: LOW. Drop the writer; the table can stay.

### D-2: Values from the strategy's own evaluation

- **Decision**: The adapter builds the record from the stop it just
  decided with. ATR and the highest high come from the same buffer, so
  `chandelier_level` equals the highest high of the chandelier window
  minus the ATR multiple. `atr` is the chandelier's ATR (§5.2 period),
  not the sizing ATR. `stop_price` is the stop in force. Until issue
  #680 lands it equals `chandelier_level`.
- **Rationale**: Recomputing in serve could drift from what the
  strategy decided. The record is a pure read of the buffers, and
  nothing in the signal path reads it back.
- **Cost of reversal**: LOW.

### D-3: Which bars get a row

- **Decision**: Rows are written on the entry bar, on each held 4h bar
  and on the exit bar. No row is written for a flat bar, a daily bar,
  or a held bar whose chandelier stop has too little history (only
  possible right after a restart with short warm-up).
  `high_since_entry` is NULL when the buffer no longer reaches the
  entry bar. The 4h buffer holds 300 bars, about 50 days, while the
  time stop is 90 days.
- **Cost of reversal**: LOW.

### D-4: Restore the stop with the position, keyed on its entry time

- **Decision**: On restart serve reads
  `latest_stop_mark(strategy_id, pair, entry_time)` and passes its
  `stop_price` as `OpenPosition.last_stop`. The read is keyed strictly
  on the position's entry time, so a new position never inherits an
  earlier one's stop. In this change the seeded stop only decides
  `stop_moved` on the first bar after a restart. `stop_moved` is NULL
  only when no previous stop is known: on the entry bar, or after a
  restart when no stop was stored for the position (one opened before
  this release).
- **Rationale**: With the stored stop, a restarted run writes the same
  rows as an unbroken run. The ratchet (issue #680) needs the same
  value to keep the stop from falling across a restart.
- **Cost of reversal**: LOW. The field is optional and defaults to None.

## Requirements

### R-1: The record matches the rule

- **A-1.1**: `uv run pytest tests/unit/domain/strategy/swing/test_stop_record.py`
  passes. On random bar paths every held bar's record equals the §5.2
  stop, ATR and high since entry computed independently. Flat bars have
  no record. The entry and exit rows carry the stops their signals
  carry.

### R-2: No signal changes

- **A-2.1**: The same file shows that signals after a restart are
  identical whatever stop was restored (property test).
- **A-2.2**: The existing swing, serve and e2e suites pass unchanged.

### R-3: Storage and restart

- **A-3.1**: `uv run pytest tests/unit/adapters/ledger/test_sqlite_ledger_stop_marks.py`
  passes. It covers an exact round trip, a replayed bar writing
  nothing, the entry-time key and an unreadable row.
- **A-3.2**: `uv run pytest tests/integration/entrypoints/test_serve_bar_loop.py -k stop_mark`
  passes. One row is written per held bar from entry to exit, and a
  restart while held writes the same rows as an unbroken run.

## Design

```text
SwingStrategyAdapter.on_bar(bar)
  last_stop_record = None
  4h, flat -> _evaluate_entry: on ENTRY  -> _record_stop(entry_time, stop)
  4h, long -> _evaluate_exit:  stop known -> _record_stop(entry_time, stop)
  _record_stop: StopRecord(stop, level=stop, ATR(22), high since entry,
                           moved = stop != _stop_in_force | None); _stop_in_force = stop
  seed(open_position) -> _stop_in_force = open_position.last_stop

_BarLoop.handle -> ... record_portfolio_mark -> _record_stop(bar)
                   -> ledger.record_stop_mark(StopMark(strategy_id, pair, bar close, record))
_BarLoop._restore_position -> ledger.latest_stop_mark(strategy_id, pair, entry_time)
                           -> OpenPosition(..., last_stop=stop_price)
```

## Test plan

1. All acceptance criteria pass.
2. Full unit, integration and e2e suites pass on the branch merged with
   current main.
3. `bash scripts/dev/pr-preflight.sh` is green.

## Risks

- A decision change by accident. The property tests cover it, and the
  parity check (issue #358) would also catch it. If missed, signals
  would differ from the backtest (F-row).
- A phantom position: an entry signal whose order is refused leaves
  the strategy long (testing audit item 4). Rows are then written for
  a position with no fill. They follow the strategy's own state, so
  they show the defect rather than hide it.
- Extra work per held bar: one ATR(22) and one max over at most 300
  highs.
