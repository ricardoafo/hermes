# Change: 1001-console-background-reads

**SDD change**: `1001-console-background-reads`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1001-console-background-reads/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The console used to read every card from its sources on Textual's UI thread,
on each screen switch and each 5 s poll, so any slow read (SQLite, the
journal, an ssh hop with `--vps`) froze keys and animations while it ran. This change moves
every read into one pure function, `read_console`, that returns a
`ConsoleReads` value. `hermes tui` runs it in a worker thread and paints when
the reads arrive, and a screen switch paints from the last reads straight
away. The main risk is a screen showing data one poll old for the length of
a read, which the next paint replaces.

## Scope

### In

- `src/hermes/entrypoints/tui_reads.py`: `ReadSources`, `ReadRequest`,
  `ConsoleReads`, `read_console`, `read_shadow`.
- `HomeApp(background_reads=...)`: reads in a worker and paints on arrival;
  `run_home` passes `True`. Without it the app reads and paints at once,
  as before, which the existing Pilot and snapshot tests rely on.
- Each source is read once per poll: the market watch and the portfolio were
  read twice.
- Cursor moves, opening and closing the detail pane, and the position
  detail screen paint from the last reads instead of reading again.
- The System screen (issue #717) reads its services, releases, machine
  and bar timing in the same read, while it shows; its EUR rate date comes
  from the last portfolio read.

### Out

- The cold start screen's time zone read at mount (one read before Home
  shows).
- Rendering cost on the UI thread; not measured as slow.
- A System service's detail screen reads that unit's journal when it opens
  and on each poll while open.

## Decisions

### D-1: One read function, one immutable result

- **Decision**: All reads go through `read_console(sources, request)`, which
  touches no widget and returns a frozen `ConsoleReads`; a failed read keeps
  its error in its own field.
- **Alternatives**: a worker per card (many paints per poll, ordering
  between cards); async model methods (the read model is synchronous
  SQLite and subprocess code).
- **Rationale**: one worker, one paint, and the read side is testable without
  Textual.
- **Cost of reversal**: LOW.

### D-2: One read in flight; a redraw asked for meanwhile reads again after it

- **Decision**: While a read runs, further redraws set a flag; when the read
  arrives it is painted and one fresh read starts.
- **Alternatives**: Textual's `exclusive=True` cancels the older worker,
  but a thread cannot be stopped, and with reads slower than the poll every
  read would be superseded and nothing would ever paint.
- **Rationale**: every read that finishes is painted, and the newest state
  always follows.
- **Cost of reversal**: LOW.

### D-3: Background reads only from the entry point

- **Decision**: `HomeApp` reads inline by default; `run_home` turns
  background reads on, as it already does for the cold start screen.
- **Alternatives**: background by default, with every Pilot test waiting on
  workers (hundreds of test edits that change no assertion).
- **Rationale**: the paint code is the same in both modes; new tests cover
  the worker path and that `run_home` uses it.
- **Cost of reversal**: LOW.

### D-4: Long-lived ledger connections may move to the worker

- **Decision**: `open_ledger(..., any_thread=True)` opens a read-only
  connection that another thread may use; the Tax source and the System
  screen's bar timing open theirs that way. A read-write connection refuses it.
- **Alternatives**: a connection per Tax read (rebuilds the tax service each
  poll); reading Tax inline (keeps a ledger read on the UI thread).
- **Rationale**: Python's SQLite is built serialized, a `mode=ro` connection
  never writes, and the console uses it from one thread at a time.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Reads leave the UI thread

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_tui_reads.py -k switch`
  passes: with a model whose heartbeat read is held, pressing 4 shows the
  Tax tab while no read has returned yet.
- **A-1.2**: The same test fails when `_redraw` reads inline.

### R-2: Each read once per poll

- **A-2.1**: `test_one_read_calls_market_watch_and_portfolio_once` passes.

### R-3: Nothing else changes

- **A-3.1**: `uv run pytest tests/unit/entrypoints tests/integration/entrypoints`
  passes with no existing test or snapshot edited.

## Design

```text
_redraw()                       UI thread: build a ReadRequest from the shown tab, range, layout
  ├─ inline:      _paint(read_console(sources, request))
  └─ background:  worker thread → read_console → call_from_thread(_reads_arrived)
_reads_arrived(reads)           UI thread: _paint(reads); read again if asked meanwhile
_switch_to(tab)                 paint the tab from the last reads, then _redraw()
```

## Test plan

1. `uv run pytest tests/unit/entrypoints tests/integration/entrypoints`.
2. `bash scripts/dev/pr-preflight.sh`.

## Risks

- A switch to a screen not yet read shows its previous content until the
  read arrives.
- A read that raises an unexpected exception ends the console, as it did
  before on the UI thread.
