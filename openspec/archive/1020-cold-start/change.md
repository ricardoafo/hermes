# Change: 1020-cold-start

**SDD change**: `1020-cold-start`
**Date**: 2026-10-17
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1020-cold-start/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes tui` opens on a cold start screen, as the console design spec's
"Start-up sequence" describes: the Winged H, the running release and one
row per start-up check (ledger, engine, journal, market feed, AI
supervisor), each turning ✓ with what it found or ✗ with why. The checks
are real reads run in a worker thread, so the screen doubles as a
connection check and never blocks the UI. The main risk is a check that
hangs; it only delays the screen's own close, and any key opens Home.

## Scope

### In

- `src/hermes/entrypoints/tui_boot.py`: the checks, the row renderer and
  `BootScreen`.
- `HomeApp(boot=...)`: pushes the screen on mount when boot and motion are
  on; `run_home` passes `boot=True`.
- A first-run row when the ledger has no bar check yet, naming when the
  first one is due.
- Pilot tests and SVG snapshots of the finished screen at the four
  breakpoints in both day themes.

### Out

- Home's per-panel empty states, which already exist.
- Moving Home's own reads off the UI thread (issue #1001).

## Decisions

### D-1: A screen pushed over Home, not a separate app

- **Decision**: `BootScreen` is pushed by `HomeApp.on_mount` and dismissed
  into the already-drawn Home.
- **Alternatives**: a separate pre-app (Home would start drawing only
  afterwards, adding its whole start time to the wait); a widget layer on
  Home (keys would reach Home's bindings while it shows).
- **Rationale**: Home draws underneath while the checks run, so closing the
  screen shows Home at once.
- **Cost of reversal**: LOW.

### D-2: Opt-in `boot` flag

- **Decision**: `HomeApp` shows the screen only when constructed with
  `boot=True` (as `run_home` does) and motion is on.
- **Alternatives**: always on with motion (every existing Pilot test would
  start under the boot screen and need rewriting).
- **Rationale**: the spec skips the sequence with motion off; tests and
  embeds keep opening straight on Home.
- **Cost of reversal**: LOW.

### D-3: Real checks in a worker thread

- **Decision**: each check is a read-model, journal or ticker call run by
  `run_worker(thread=True)`; any exception becomes a failed row.
- **Alternatives**: a timed animation with fixed "ok" rows (a fake check
  would say ok while the ledger is unreadable).
- **Rationale**: the spec makes the sequence a real connection check; the
  console's read-only rule (MVP1) holds because every check is a read.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The screen shows real check results

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_tui_boot.py -k "row or ledger or engine or journal or ticker or feed"` passes.

### R-2: It never blocks or traps the operator

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_tui_boot.py -k "thread or key or quits or closes or motion"` passes.

### R-3: Its look is reviewed

- **A-3.1**: `uv run pytest tests/unit/entrypoints/test_tui_boot.py -k snapshot` passes against the committed SVGs.

## Design

```text
BootScreen(checks, tokens, release=..., hold=0.8)
  compose: Logo · "release <name>" · checklist · "any key opens the console · Q quits"
  on_mount: run_worker(_run_checks, thread=True)
  _run_checks: for each check -> run_check -> call_from_thread(_show)
               then call_from_thread(set_timer, hold, _close)
  on_key: Q quits, any other key closes
boot_checks(model, tz=, journal=, ticker=) -> (ledger, engine, journal, market feed, AI supervisor)
```

## Test plan

1. Every acceptance criterion above passes.
2. The console and TUI suites stay green with their snapshots unchanged.

Test tiers touched: unit

## Risks

- **A hanging check** (for example a network stall on the ticker) keeps
  the screen up until its timeout; any key opens Home meanwhile, and the
  ticker's own timeout bounds it.
- **Reads on two threads**: the checks read while Home's first redraw
  reads on the UI thread. Each read opens its own read-only connection.

## Dependencies on other changes

- None. PR #990 (top bar) also edits `tui.py`; this change adds one
  constructor flag and four lines to `on_mount`.
