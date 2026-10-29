# Change: console-panel-registry

**SDD change**: `console-panel-registry`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#525)

Mirror of engram topic key `sdd/console-panel-registry/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`console.py` holds a literal `CONSOLE_COMMANDS` set and the `run_*` functions of
the positions and tui panels, so every console PR edits it. After this change a
console panel is one module under `hermes.entrypoints.commands`: its `Command`
sets `wants_demo`, and `console.py` derives the set of console commands from the
command registry. `run_positions` and `run_tui` move into their own modules.
There is no behaviour change; all output, `--demo`, `HERMES_CONSOLE_DEMO` and
`resolve_read_model` stay byte-identical.

## Scope

### In

- `console.py` keeps the demo switch, `resolve_read_model` and the overview.
  `CONSOLE_COMMANDS` is replaced by `console_commands()`, read from `discover()`.
- `run_positions` moves to `commands/positions.py`, `run_tui` to `commands/tui.py`.
- A registry test that fails if `console.py` or `cli.py` names a console command.

### Out

- Shared renderers (`application/console/render.py`); splitting them is a later step.

## Decisions

### D-1: `wants_demo` is the console marker

- **Decision**: A command is a console command exactly when it takes the demo
  scenario (`wants_demo=True`); no second flag.
- **Alternatives**: a new `console=True` field (two flags that must always agree);
  a new `entrypoints/console/` package with its own discovery (second registry).
- **Rationale**: `wants_demo` already means "runs on a read model that `--demo`
  can replace", which is the definition of a console command.
- **Cost of reversal**: LOW.

### D-2: Lazy registry import in `console.py`

- **Decision**: `console_commands()` imports `discover` inside the function.
- **Alternatives**: top-level import (cycle: command modules import `console`).
- **Rationale**: keeps `resolve_read_model` importable by command modules.
- **Cost of reversal**: LOW.

## Requirements

### R-1: No shared list of console commands

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_console_registry.py -q` exits 0.
- **A-1.2**: `rg 'CONSOLE_COMMANDS' src` has no match.

### R-2: No behaviour change

- **A-2.1**: `uv run pytest tests/unit tests/integration -q` exits 0 with no
  assertion changed (import paths of `run_positions` and `run_tui` only).

## Design

- `console.console_commands() -> frozenset[str]`: names of discovered commands with `wants_demo`.
- `apply_demo_flag` uses it to refuse `--demo` before a non-console command.
- `commands/positions.py` and `commands/tui.py` hold their `run_*` plus `COMMAND`.

## Test plan

1. `test_console_registry.py` plus the existing console, positions, tui, status,
   activity tests pass unchanged apart from import paths.
2. ruff, mypy, lint-imports clean.

Test tiers touched: unit

## Risks

- **Risk 1**: `discover()` imports every command module on `--demo X cmd`. It
  already runs right after for dispatch, and modules are cached, so no extra cost.

## Dependencies on other changes

- Builds on #457 (command registry).
