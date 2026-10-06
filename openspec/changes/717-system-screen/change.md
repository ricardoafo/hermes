# Change: 717-system-screen

**SDD change**: `717-system-screen`
**Date**: 2026-10-06
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/717-system-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The System screen of the Cockpit console (design spec
`docs/design/console-cockpit.md`, screen 7), view only in MVP1. Tab 7 replaces
its "coming next" placeholder with five `HudPanel`s: Services (systemd state
and next timer run of hermes, the bot, health, daily, backup, archive check and
tax pack), Releases (live release first, then the others on disk), Data and
timing (market data, order book and EUR rate freshness, bar-check time
sparkline), Machine (cpu, memory and disk meters, ledger size, last snapshot)
and Actions (bootstrap, restart, rollback and a read-only diagnosis as commands
to copy). A release opens a `DetailScreen` under `◂ System › Releases › <sha>`
and a service a `DetailPane` (a `DetailScreen` on compact terminals), reusing
the drill-down of issue #730. Four small read-only ports feed it; their adapters read
systemd, the release directory, `/proc` and the ledger, and answer "not
available here" off the VPS. The main pick is that nothing runs from the
screen; the main risk is `systemctl show` output differing between systemd
versions.

## Scope

### In

- `src/hermes/application/console/system.py`: view objects (`ServiceUnit`,
  `ServiceStatus`, `LiveRelease`, `ReleaseEntry`, `ReleaseHistory`,
  `MachineStats`, `CopyCommand`), the ports `ServiceReader`, `ReleaseReader`,
  `MachineReader`, `BarTimingReader`, the `SystemSources` bundle, the unit
  list, the VPS actions, the deploy steps and `age_text`.
- `src/hermes/adapters/system/`: `SystemctlServiceReader` (`systemctl show
  --timestamp=unix`), `ReleaseDirectoryReader` (`/opt/hermes/versions`,
  `previous`), `ProcMachineReader` (`/proc/loadavg`, `/proc/meminfo`,
  `shutil.disk_usage`, ledger size, newest `*.sqlite3` snapshot).
- `SqliteStatusReader.bar_check_seconds` (ledger, read-only);
  `JournalctlReader.unit` (journal of another hermes unit).
- `src/hermes/entrypoints/tui_widgets.py`: `meter_text`, the one gradient meter.
- `src/hermes/entrypoints/tui_system.py`: the panel and detail text builders.
- `src/hermes/entrypoints/tui.py`: tab 7 layout, cursor over services then
  releases, Enter / Esc, `system=` on `HomeApp` and `run_home`.
- `src/hermes/entrypoints/console.py` + `commands/tui.py`: `system_sources`
  wiring and `recent_unit_journal_lines`.

### Out

- The keyed actions B / R / K with a typed confirmation, the bar-close hold and
  the journal entry: MVP2.
- Step timings of a deploy and the PRs / changelog of a release: not recorded
  on the VPS; the detail says so.
- Order book freshness: the order book is fetched only when a signal fills and
  is not recorded; the panel says "not available yet".
- The 2 s live machine refresh (spec "System live"): the screen follows the
  console's 5 s poll.

## Decisions

### D-1: four small read-only ports in the application layer, adapters behind them

- **Decision**: `ServiceReader`, `ReleaseReader`, `MachineReader` and `BarTimingReader` live beside `ConsoleReadModel` in `hermes.application.console`; the TUI gets a `SystemSources` bundle and imports no adapter (the existing `test_tui_module_imports_no_adapter_or_port` still holds). The entrypoint `console.system_sources` builds the adapters.
- **Alternatives**: extend `ConsoleReadModel` (every fixture and the ledger read model would have to answer systemd questions); call `systemctl` from the TUI (breaks the hexagonal rule).
- **Rationale**: tests pass fakes; off the VPS each reader answers on its own.
- **Cost of reversal**: LOW.

### D-2: nothing runs from the screen

- **Decision**: each VPS action is a `CopyCommand` drawn as `$ command`; no key is bound to an action, and the screen calls no process starter. The only processes are the readers' own read-only `systemctl show` and `journalctl`, behind the ports.
- **Alternatives**: bind B / R / K now with a confirmation box (the Code Owner moved these to MVP2 on 2026-10-05).
- **Rationale**: the hard rule "read-only"; a test patches every process starter to fail and walks the screen with every key.
- **Cost of reversal**: MEDIUM (MVP2 adds the confirmation and the journal entry).

### D-3: the live release comes from `read_release`, handed in

- **Decision**: `ReleaseDirectoryReader(live=...)`; `commands/tui.py` passes a wrapper over `read_release()`, the same reader `hermes status` uses. The adapter imports no entrypoint (`adapters-never-import-entrypoints`).
- **Alternatives**: move `read_release` into an adapter (touches `hermes status` and `hermes version`).
- **Rationale**: one source for "which release is live"; smallest change.
- **Cost of reversal**: LOW.

### D-4: one `systemctl show` call with unix timestamps

- **Decision**: `systemctl show --timestamp=unix --property=Id,LoadState,ActiveState,SubState,StateChangeTimestamp,NextElapseUSecRealtime,LastTriggerUSec` for every service and timer in one call; `None` without systemctl, on failure, or when no hermes unit is installed.
- **Alternatives**: `systemctl list-timers --output=json` plus one `show` per unit (more calls, JSON output only in newer systemd).
- **Rationale**: one short read per 5 s poll; unix times need no locale or zone parsing; `show` needs no root, so it works as the `hermes` user over `hermes --vps`.
- **Cost of reversal**: LOW.

### D-5: bar-check time is recorded_at minus the bar close

- **Decision**: `bar_check_seconds` reads the newest `BarProcessed` rows and returns `recorded_at - occurred_at` in whole seconds, oldest first; the screen draws the last 30 as a sparkline with the last and the maximum.
- **Alternatives**: a new timing event (schema change for a display figure).
- **Rationale**: both times are already on every row; no ledger change.
- **Cost of reversal**: LOW.

### D-6: one meter, in `tui_widgets`

- **Decision**: `meter_text(fraction, tokens, cells, warn_from)` draws `▕████░░▏` with the `gain` gradient, amber from its warning level; the cpu, memory and disk meters use it, and #712 / #714 reuse it.
- **Alternatives**: a meter inside `tui_system.py` (a second variant later).
- **Rationale**: the spec's "one implementation each".
- **Cost of reversal**: LOW.

## Requirements

### R-1: panels

- **A-1.1**: tab 7 shows Services, Releases, Data and timing, Machine and Actions from the readers; Services lists the seven units in spec order with state and next timer run.
- **A-1.2**: a missing reader, or one that answers `None`, shows "not available here" in its panel; a missing machine figure shows it on its own row; missing freshness or bar timing shows "not available yet".

### R-2: drill-down

- **A-2.1**: Enter on a release opens a `DetailScreen` whose first row reads `◂ System › Releases › <sha>`, with the rollback target (live release: the `previous` target) and the ten deploy steps; `]` moves to the next release; Esc returns with the cursor on the release shown last.
- **A-2.2**: Enter on a service opens a `DetailPane` from 120 columns and a `DetailScreen` (`◂ System › Services › <name>`) below; Esc closes the pane; the pane reads that unit's journal.

### R-3: view only

- **A-3.1**: with `subprocess`, `os.system` / `popen` / `exec*` and asyncio's process starters patched to fail, walking the screen with B R K, Space, arrows, Enter, `]` and Esc starts no process.
- **A-3.2**: no binding uses B, R or K (lower-case k stays the cursor key), and no action names bootstrap, restart, rollback, run or execute.

### R-4: adapters

- **A-4.1**: the service reader issues one `systemctl show` with no unit-changing verb, reads states and armed timers, and returns `None` without systemctl, on failure, or with no hermes unit installed.
- **A-4.2**: the release reader lists releases newest install first, unfinished last, reads `previous`, changes nothing on disk, and returns `None` without `versions/`.
- **A-4.3**: the machine reader computes load per cpu, memory used and disk used, and returns `None` per figure it cannot read.
- **A-4.4**: `bar_check_seconds` returns whole seconds oldest first, empty before the first bar, and raises `LedgerError` on an unreadable time.

### R-5: snapshots

- **A-5.1**: SVG snapshots of tab 7 at 80x24 and 160x45 with motion off and a frozen clock; the existing console snapshots are unchanged.

All of R-1..R-5: `uv run pytest -q tests/unit/entrypoints/test_tui_system.py tests/unit/adapters/system tests/unit/application/console/test_system.py tests/unit/adapters/ledger/test_status_reader.py`.

## Design

```text
application/console/system.py   view objects, 4 ports, SystemSources, VPS_ACTIONS, DEPLOY_STEPS
adapters/system/services.py     SystemctlServiceReader  (systemctl show, read-only)
adapters/system/releases.py     ReleaseDirectoryReader  (versions/, previous; live injected)
adapters/system/machine.py      ProcMachineReader       (/proc, disk_usage, ledger, snapshots)
adapters/ledger/status_reader   + bar_check_seconds
adapters/systemd_journal        + JournalctlReader.unit
entrypoints/tui_widgets.py      + meter_text
entrypoints/tui_system.py       services_text, releases_text, release_detail, service_detail,
                                timing_text, machine_text, actions_text
entrypoints/tui.py              tab 7 layout and keys; HomeApp(system=...)
entrypoints/console.py          system_sources(db_path, live, ledger), recent_unit_journal_lines
```

Tab 7 layout: below 120 columns one column (Services, Releases, Actions, then
the service pane, Data and timing, Machine); from 120 columns two: Services,
Releases and Actions left, the service pane, Data and timing and Machine right.
The 5 s poll re-reads the sources only while tab 7 shows; moving the cursor
only repaints. ↑↓ walk one cursor over the services, then the releases.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.

Test tiers touched: unit, property

## Risks

- **systemd output differs between versions**: `--timestamp=unix` needs systemd 248+ (the VPS runs a newer one); an unparsable time shows as "next unknown", not an error.
- **The `hermes` user cannot read another unit's journal**: the service pane then shows no lines or "not available here"; nothing breaks.
- **`systemctl` hangs**: the call has a 5 s timeout and then reads as "not available here".

## Dependencies on other changes

- Builds on issue #711 (theme, `HudPanel`) and issue #730 (`DetailPane`, `DetailScreen`, `Breadcrumb`). Uses the release reader of PR #647. MVP2 adds the keyed actions.
