# Change: 1184-home-app-controllers

**SDD change**: `1184-home-app-controllers`
**Date**: 2026-10-19
**Status**: In progress (batch 1: System tab)
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1184-home-app-controllers/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`HomeApp` in `entrypoints/tui.py` is one class that holds the state and the
painters of every tab, so every console PR edits it. Each tab moves, one
batch at a time, into a controller next to its `tui_<screen>.py`;
`HomeApp` stays the router and the timer owner. Batch 1 moves the System
tab. No visual change: every SVG snapshot must match unchanged.

## Scope

### In

- Batch 1: `SystemTabController` and `SystemFrame` in
  `tui_system_tab.py`; the System reads, cursor and service pane leave
  `HomeApp`.
- A test that pins the list of `_paint*` methods on `HomeApp` so it only
  shrinks.

### Out

- The other tabs, the effects helper, the status row widget and
  `ConsoleSources`: later batches under the same issue.
- Size and complexity budgets (issue #1201).

## Decisions

### D-1: The controller gets narrow callables, not the app

- **Decision**: the controller takes a per-paint `SystemFrame` and four
  callables (home screen, set content, push screen, frame).
- **Alternatives**: pass `HomeApp` itself (circular import, and the
  controller could reach every private member).
- **Rationale**: the controller is testable with stubs and cannot grow
  back into the app class.
- **Cost of reversal**: LOW.

## Requirements

### R-1: No visual change

Every existing SVG snapshot and System test passes unchanged.

### R-2: No new painter on the app class

The `_paint*` methods on `HomeApp` are a fixed list that only shrinks.

## Design

`HomeApp._init_system` builds the controller with `_system_frame`,
`_set` and `push_screen`. The tab paint, `Esc`, the selection keys and
`Enter` call `keep`, `paint`, `close_service`, `step` and `open_detail`.

## Test plan

- `tests/unit/entrypoints/test_tui_system_tab.py`: cursor start, wrap both
  ways, empty read, pane open and close, compact detail screen.
- `tests/unit/entrypoints/test_tui_structure.py`: R-2.
- The existing `test_tui_system.py` and its snapshots cover R-1.

## Risks

- A state read that was an attribute on the app (`_services`, `_releases`)
  now lives on the controller; no test or module outside `tui.py` read
  them (checked by search).
