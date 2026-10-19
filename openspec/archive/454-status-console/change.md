# Change: 454-status-console

**SDD change**: `454-status-console`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/454-status-console/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Joins the two descriptions of "is hermes processing bars": `hermes status`'s
`StatusReport` (#370) and the console's `Heartbeat` (#384).
`heartbeat_from_report` maps one onto the other, so a ledger-backed console
read model, the TUI and Telegram `/status` will show exactly the verdict
`hermes status` prints. `status` also joins the console commands: with
`--demo` (or `HERMES_CONSOLE_DEMO`) it shows the scenario's heartbeat line,
tagged DEMO DATA, with the matching exit code. Without a demo nothing changes.

## Scope

### In

- `application/console/heartbeat.py`: `heartbeat_from_report`.
- `hermes status` demo path: heartbeat line or `--json`, exit 0 OK, 1 late or
  down, 2 unknown scenario.
- `status` in `CONSOLE_COMMANDS`; the CLI passes a leading `--demo` scenario.

### Out

- The ledger-backed `ConsoleReadModel` itself (it will call
  `heartbeat_from_report` for its `heartbeat()`).

## Decisions

### D-1: down since = last check

systemd reports that the unit is down, not when it stopped. The last processed
close is the latest moment hermes is known to have worked, so a down heartbeat
reads "down since" that time; the heartbeat requires one when down.

### D-2: demo shows the heartbeat renderer

The fixture scenarios carry a `Heartbeat`, not a `StatusReport`, so the demo
path renders `render_heartbeat` (the console's status line) instead of
inventing streams. The live path keeps the full `hermes status` view.

### D-3: same exit codes as the live command

0 when OK, 1 when late or down, so scripts can test the demo path the same way.

## Requirements

### R-1: heartbeat state equals the verdict for ok, late and down

### R-2: last check = newest close; next check = earliest next close

### R-3: `down_since` set exactly when down

### R-4: `hermes --demo SCENARIO status` and `HERMES_CONSOLE_DEMO` work, tagged

### R-5: the live path is unchanged

## Design

`hermes status` checks for a demo scenario after parsing; if present it
resolves the fixture read model and prints its heartbeat. Otherwise
`_ledger_status` runs the existing path. `heartbeat_from_report` lives in the
application layer next to the console views.

## Test plan

- `tests/unit/application/console/test_heartbeat.py`: R-1 to R-3 and the empty
  report.
- `tests/unit/entrypoints/test_status.py`: demo per scenario through the CLI
  (R-4), the environment variable, an unknown scenario; the existing tests
  cover R-5.

## Risks

- Same `cli.py` lines as #453 (`hermes activity`); merged after it, with the
  shared `--demo` helper kept once.

## Dependencies on other changes

- #370 (status layer), #384 (console read model and fixtures).
