# Change: 376-hermes-logs

**SDD change**: `376-hermes-logs`
**Date**: 2026-10-03
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/376-hermes-logs/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes logs` makes hermes's journal readable: it runs
`journalctl -u hermes.service -o json` and prints one line per entry,
`HH:MM:SS  LEVEL  event  key=value...  …corrtail`, with `--level`, `--since`,
`--cycle`, `--last`, `-f` and `--json`. ADR-008 lists the command as pending.
Parsing and rendering live in the console application layer so the TUI's log
screen (#377) and Telegram `/logs` (#379) show the same lines. Main risk: none
beyond the journal being unavailable off the VPS, which exits 2 with a clear
message.

## Scope

### In

- `application/console/logs.py`: `LogLine`, `LogLevel`,
  `parse_journal_entry`, `filter_lines`, `render_log_line`,
  `log_line_to_json`.
- `adapters/systemd_journal/journalctl.py`: `JournalctlReader` (argv list,
  injectable process spawner).
- `entrypoints/logs.py` and `entrypoints/commands/logs.py`.
- Runbook: "Check on hermes" in `docs/guides/vps-operations.md`.

### Out

- Journal rows in `hermes activity` (restarts, warnings, errors): a follow-up
  that reuses this reader.

## Decisions

### D-1: journalctl, not the journald Python bindings

`journalctl -o json` is on every systemd host and needs no new dependency; the
command runs as the `hermes` user, so journal access is the operator's call
(the `systemd-journal` group or `sudo`).

### D-2: the message is structlog JSON; everything else is raw

Prod logs are JSON (ADR-008 §1). A message that is not a JSON object with an
`event` key (systemd's own lines, tracebacks) prints as is. Nothing is added
or removed, so the output never carries a field hermes did not log.

### D-3: relative `--since`

`1h`, `30m` and `2d` become journalctl's `-1h`; anything else (a timestamp,
`today`) passes through.

### D-4: Madrid time by default

Like every console screen; `--utc` switches.

## Requirements

### R-1: one line per entry in journal order; raw lines pass through

### R-2: `--level X` keeps X and worse; raw lines count as info

### R-3: `--cycle` matches the full correlation id or its tail, case-insensitive

### R-4: fixed journalctl argv, no shell

### R-5: no journalctl means exit 2 with a message

### R-6: `--json` gives the same entries as data; no P&L in any output

## Design

`main` builds the reader's line iterator, parses each envelope, filters, and
prints with Rich (colour on a terminal only) or as JSON lines. The reader
terminates journalctl when iteration stops, so `-f` ends cleanly on Ctrl-C.

## Test plan

`tests/unit/entrypoints/test_logs.py` with a fake process: R-1 to R-6, Madrid
time, absolute `--since`, usage errors, the registry entry, non-envelope
lines, unknown levels.

## Risks

- `-f` holds a journalctl process; the reader terminates it when the command
  exits or is interrupted.

## Dependencies on other changes

- #460 (command registry), #384 (console layer).
