# Change: 1151-lab-memory-watch

**SDD change**: `1151-lab-memory-watch`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1151-lab-memory-watch/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-024 §6 says the staging lab alerts when available memory drops below
1 GiB, and sized the shadow line from a single `hermes serve` run. Nothing
measured memory with every shadow running and nothing alerted. A oneshot
unit on a one-minute timer now reads `MemAvailable` and each running
`hermes-shadow@` instance's `MemoryCurrent`, logs them on one journal line,
and sends one Telegram message on the way below 1 GiB and one on the way
back. The main risk is a watch that pages every minute; a state flag that
changes only after a successful send keeps it to one message per crossing.

## Scope

### In

- `deploy/hermes-lab-memory.sh`: the read, the log line and the alert.
- `deploy/systemd/hermes-lab-memory.{service,timer}`: run as
  `hermes-staging` with `env.staging`, every minute, off until enabled.
- Unit tests that run the real script with a stand-in `systemctl` and a
  loopback Telegram endpoint.
- `deploy/README.md` entries, including the enable step and the journal
  query that gives the day's figures.

### Out

- Enabling the timer on hermes-01 (Rick's machine step).
- Recording the one-day figures in issue #1151, which needs the shadow
  line from issue #1076 running first.
- Any prod unit or prod alert path.

## Decisions

### D-1: bash + curl + systemctl, not a `hermes` subcommand

- **Decision**: the watch is a shell script, like `hermes-notify-failure.sh`.
- **Alternatives**: a `hermes lab memory` command (needs the release venv).
- **Rationale**: the alert matters most when the box is under pressure or a
  release is broken; it must not depend on the Python environment.
- **Cost of reversal**: LOW.

### D-2: Alert on the crossing, not on every low reading

- **Decision**: a flag file in `StateDirectory` records "alerted low"; it is
  written or removed only after Telegram accepts the message.
- **Alternatives**: alert every run (a page a minute); write the flag first
  (a failed send would be lost).
- **Rationale**: one message per crossing, and a failed send retries on the
  next run.
- **Cost of reversal**: LOW.

### D-3: The staging chat, through `env.staging`

- **Decision**: the unit reads `/etc/hermes/env.staging` and uses its
  optional Telegram chat. With no chat set, a due alert exits 1 so the unit
  shows as failed rather than silently dropping the alert.
- **Alternatives**: prod's chat (mixes lab alerts with real ones, which the
  staging guide forbids).
- **Rationale**: keeps staging alerts on staging's own channel.
- **Cost of reversal**: LOW.

## Requirements

### R-1: One alert per crossing of 1 GiB

1023 MiB available sends one low message, 1024 MiB sends none, a long low
spell sends one, and a recovery sends one and re-arms the alert.

### R-2: No lost alert

A send that fails leaves the state unchanged, so the next run sends it.

### R-3: The figures ADR-024 §6 asks for

Every run logs available memory, the shadows' total and each instance's
`MemoryCurrent`; a unit without memory accounting is left out of the total.

### R-4: The token stays off argv

The bot token reaches curl through `--config -` on stdin.

## Design

The script reads `MemAvailable` (kB) from `/proc/meminfo`, lists running
units with `systemctl list-units 'hermes-shadow@*'`, and reads each one's
`MemoryCurrent` with `systemctl show`, skipping values that are not plain
byte counts (systemd prints all-ones or `[not set]` without accounting). It
prints one `hermes-lab-memory:` line, compares available MiB against 1024,
and sends the low or recovery message when the flag disagrees with the
reading. The unit runs as `hermes-staging` with
`StateDirectory=hermes-lab-memory`, the usual hardening, and
`ReadWritePaths` limited to that directory.

## Test plan

`tests/unit/deploy/test_lab_memory_script.py` runs the real script under
bash with the real curl against a loopback HTTP server, one test per
requirement. `tests/unit/deploy/test_systemd_units.py` already checks every
unit's hardening and env file; the lab unit is added to its env-file map.

## Risks

- A low spell with no Telegram chat configured fails the unit each minute.
  That is intended: `systemctl --failed` shows the unsent alert.

## Dependencies on other changes

- Issue #1076 (PR #1134) starts the `hermes-shadow@` instances this watch
  measures. The watch runs without them and logs a zero total.
