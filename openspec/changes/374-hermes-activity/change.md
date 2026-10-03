# Change: 374-hermes-activity

**SDD change**: `374-hermes-activity`
**Date**: 2026-10-03
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/374-hermes-activity/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes activity` prints the console's day-grouped feed of what changed,
newest first: opened, closed (with net), folded stop raises, restarts,
warnings, errors. Bar checks and signals only appear with `--all`. With
`--demo` it renders the #384 fixture scenarios through the existing
`render_activity`; without it, it reads the ledger read-only and turns entry
and exit signals into opened and closed rows, the only position events the
ledger holds before simulated fills (#353). Main risk: the ledger-backed rows
are thinner than the design (no net, no stop raises, no journal rows) until
fills and the journal reader land.

## Scope

### In

- `src/hermes/entrypoints/activity.py`: `run_activity`, flags `--all`,
  `--json`, `--utc`, `--db`, `--days`.
- `application/console/activity.py`: `group_activity` and `activity_to_json`.
- `adapters/console/ledger_activity.py`: `LedgerActivityReader` (`mode=ro`).
- `activity` joins `CONSOLE_COMMANDS`; the CLI passes a leading `--demo`
  scenario to it (same helper as #437).

### Out

- Journal rows (restarts, warnings, errors) from the live system: #376.
- Net on closed rows and stop raises from live data: need fills (#353) and the
  per-bar stop record.
- A full ledger-backed `ConsoleReadModel`.

## Decisions

### D-1: live data from signals, not an empty state

The ledger already holds the shadow position's opens and closes as entry and
exit signals, so the feed shows them rather than "no data yet". These rows
carry no price or net: the console shows prices in EUR, and a signal has only
the USDT close with no EUR rate, so no figure beats a mislabelled one. EUR
prices and net arrive with fills (#353) and EUR ledger values (#386).

### D-2: one section, reusing the #384 renderer

The command prints `render_activity` only, like `hermes positions` (#437); the
status line and portfolio belong to `hermes` and the TUI.

### D-3: `--days` window for the ledger

The ledger feed reads the last 7 days by default, so `--all` stays readable
(six bar rows per 4h at the current universe).

### D-4: money follows `pnl_visible`

The JSON form puts a closed row's net under `net_eur` only on operator
surfaces; the renderer already hides it on blind ones.

## Requirements

### R-1: day-grouped, newest first, in the display timezone

### R-2: quiet rows only with `--all`

### R-3: repeated stop raises arrive folded (fixture data) and render folded

### R-4: read-only ledger; a missing path exits 3 and creates nothing

### R-5: `--json` carries the same rows; net only where `pnl_visible`

Prices keep the USDT quote under `price` and the EUR figure the screen shows
under `price_eur` (stop levels likewise).

### R-6: `hermes --demo SCENARIO activity` works through the top-level CLI

## Design

`run_activity` resolves the read model; a demo model renders its own
`activity()`, otherwise `LedgerActivityReader.items_since(now - days)` feeds
`group_activity`. Both paths render with `render_activity` or serialise with
`activity_to_json`.

## Test plan

- `tests/unit/application/console/test_activity.py`: R-1, R-2, R-5.
- `tests/unit/entrypoints/test_activity.py`: syrupy snapshots per scenario at
  80 and 120 columns, DEMO DATA tag and folding (R-3), quiet rows (R-2),
  ledger rows from signals, read-only write attempt and missing ledger (R-4),
  usage errors, the CLI path (R-6).

## Risks

- Two console PRs (#437 and this) add the same `--demo` helper to `cli.py`;
  whichever merges second resolves a trivial conflict.

## Dependencies on other changes

- #384 (read model, fixtures, renderer), #370 (test ledger helpers).
