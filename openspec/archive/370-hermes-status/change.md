# Change: 370-hermes-status

**SDD change**: `370-hermes-status`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/370-hermes-status/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes status` answers "is hermes processing bars, and when is the next
one?" without touching the ledger it reads. It leads with the verdict every
console surface will share (all good, late, down), with a plain-words reason
and the next step, then one row per stream with its last and next close in UTC
and Madrid time, signal counts and open shadow entries. The verdict lives in a
new application module so the console read model (#384), the TUI (#377) and
Telegram `/status` (#379) reuse it instead of re-deriving it. Main pick: a
separate read-only port and adapter rather than `SqliteLedger`, whose
constructor migrates. Main risk: a wrong verdict rule would mislead every
surface at once; the rules are pinned by unit and property tests.

## Scope

### In

- Port `StatusReadPort` (`ports/status.py`) with `ShadowEntry`.
- Adapter `SqliteStatusReader` (`adapters/ledger/status_reader.py`): `mode=ro`
  URI, no migrations, `FileNotFoundError` on a missing file.
- Application `hermes.application.status`: `build_report`, the verdict rules,
  `ledger_missing_report`.
- Renderers `hermes.entrypoints.status_view`: Rich human view and a
  JSON dict.
- CLI `hermes status [--config-dir] [--db] [--json]`, with a `systemctl show`
  probe for the unit state.
- vps-diagnose runs `hermes status` as the `hermes` user.
- `rich` as a direct dependency (already installed through Textual).

### Out

- The console read model, fixtures and `--demo` (#384), `hermes activity`
  (#374), positions (#375), the TUI (#377), Telegram (#379).
- The healthchecks progress ping (#372), which will call `hermes status --json`.
- Any P&L: this command shows entry facts only.

## Decisions

### D-1: separate read-only port and adapter

`SqliteLedger.__init__` runs migrations, which is a write. A status command
must be safe next to a live `hermes serve` and must not create a file on a
wrong path, so it opens the database with `mode=ro` through its own adapter.
The adapter checks the file exists first, because SQLite would otherwise
report a generic "unable to open" error.

### D-2: verdict rules in the application layer

Down when systemd says the unit is failed, restarting after a crash, or
stopped; late when any stream is past its due time plus grace or has never
processed a bar; otherwise all good. Down is checked first so fresh ledger rows
cannot hide a dead process. With no systemd (a dev box) the unit state is
`unknown` and the ledger alone decides. The rules sit in
`hermes.application.status` so every surface agrees.

### D-3: due = next boundary + settle, late = due + grace

The ledger stores Binance's inclusive close (boundary minus 1 ms). The next
bar closes one interval after the boundary and is delivered
`settle_delay_seconds` later; it counts as late after a further
`stale_after_seconds`, the same threshold the live feed uses to call itself
stale. This matches #371's `serve.bar.next_due`.

### D-4: renderers in the entrypoint layer

Rich is presentation, so the renderers live in `hermes.entrypoints.status_view`,
next to the CLI, not in the application layer (import-linter keeps the
application layer free of UI libraries). The JSON form is built from the same
report, so the two cannot drift. On a terminal the verdict is a coloured
panel; off a terminal, or with `NO_COLOR`, Rich drops colour and the verdict
prints as plain lines.

### D-5: exit codes

0 all good; 1 late or down; 2 usage; 3 config error or no ledger. A missing
ledger still prints the down verdict, so a script reading the output and a
person reading the screen see the same thing.

## Requirements

### R-1: read-only

The reader cannot write: a write through its connection raises, and opening a
missing path creates nothing.

### R-2: per-stream progress

For each configured pair and interval: last close, next close, due time, state
(`on time`, `late`, `no data`) and how late.

### R-3: verdict

The report carries level, headline, reason and next step per D-2, with the
late headline naming the check in Madrid time ("The 22:00 check is 20 minutes
late").

### R-4: times

The human view shows UTC and Europe/Madrid times.

### R-5: no P&L

Neither the text nor the JSON output contains a P&L, return, equity, Sharpe,
drawdown or win-rate field.

### R-6: exit codes per D-5

## Design

`entrypoints/status.py` parses arguments, loads config, probes the unit,
opens `SqliteStatusReader`, calls `build_report`, and hands the report to
`render_status` or `report_to_dict`. The top-level CLI dispatches `status` to
it. vps-diagnose runs it with the env file loaded and `COLUMNS=120`.

## Test plan

- `tests/unit/application/status/test_report.py`: stream due times, the late
  boundary (exactly at due + grace is on time), never-processed streams,
  down over current streams for each unit state, unknown unit falls back,
  signal windows, missing ledger, a Hypothesis property for R-2/R-3.
- `tests/unit/adapters/ledger/test_status_reader.py`: R-1, newest-wins per
  stream, inclusive signal bound, open entries.
- `tests/unit/entrypoints/test_status.py`: exit codes on current, overdue,
  empty, failed-unit, missing-ledger and broken-config fixtures (R-6), UTC
  and Madrid times (R-4), JSON facts, no P&L in either output (R-5), syrupy
  snapshots of the plain and terminal views, the systemd mapping.

## Risks

- The unit probe needs `systemctl` and may need privileges on some hosts; on
  failure it reports `unknown` and the ledger decides.
- Rich output width follows the terminal; vps-diagnose pins 120 columns.
- `open_entries` reads one strategy per pair, which is all the shadow roster
  has today.

## Dependencies on other changes

- #371 logs the same due time in the journal.
- #384 builds the console read model on `build_report` and these renderers.
- #372 will run `hermes status --json` before each healthchecks ping.
