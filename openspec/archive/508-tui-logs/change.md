# Change: 508-tui-logs

**SDD change**: `508-tui-logs`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/508-tui-logs/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes tui`'s `L` key stops showing the "Logs arrive with #376" placeholder
and opens the last 50 journal lines, rendered exactly as `hermes logs` prints
them. The journal source is injected from the console composition, so the TUI
still imports no adapter.

## Scope

### In

- `HomeApp(journal=...)` and the logs panel in `entrypoints/tui.py`.
- `recent_journal_lines` in `entrypoints/console.py`, wired into `run_tui` in
  `entrypoints/commands/tui.py`.

### Out

- Filters in the panel (level, cycle): `hermes logs` has them.

## Decisions

### D-1: inject the source

`tui.py` must not import adapters (its own test enforces that), so the
composition passes a `count -> lines | None` callable built on
`JournalctlReader`.

### D-2: demo data shows no journal

A demo scenario has no journal; showing the host's real logs under a DEMO
DATA tag would mislead, so the panel says so.

## Requirements

### R-1: `L` shows the last 50 lines like `hermes logs`, Madrid time

### R-2: no journalctl shows a one-line notice

### R-3: demo data shows a one-line notice

### R-4: the panel refreshes with the home screen

## Design

`action_logs` pushes a panel whose body calls `_logs_content`; the existing
refresh tick rebuilds open panels.

## Test plan

`tests/unit/entrypoints/test_tui.py`: pilot tests for R-1 to R-3;
`tests/unit/entrypoints/test_console_journal.py`: the composition's source.
The placeholder test is replaced, because the placeholder is gone by design.

## Risks

None: read-only, and a failing journal read shows a notice.

## Dependencies on other changes

- #376 (journal reader), #469 (TUI), #491 (TUI refresh fix, merged first).
