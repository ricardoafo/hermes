# Change: 653-live-activity

**SDD change**: `653-live-activity`
**Date**: 2026-10-05
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/653-live-activity/change`.

## Summary

The activity feed showed bars, signals and trades only. It now adds restarts
(naming the release), deploys, warnings and errors read from the journal that
`hermes logs` already reads, and folds every 4h candle into one quiet line
such as `checked 3 coins · no signal`. Read-only, no new service, no ledger
write. When the journal is not readable (a dev box) the journal rows are
simply omitted. The main risk is leaking a value through a warning's fields;
only the event name, coin and error class are copied.

## Scope

### In

- `serve.run.started` logs `release` (the `current` symlink target).
- `application/console/journal_activity.py`: journal lines to restart, deploy,
  warning and error rows; yes/no entry-condition counts; `fold_checks`.
- `LedgerReadModel` takes an optional journal source (30 s cache) and merges it.
- `hermes activity` and `hermes tui` use `journal_window_lines`; `--vps` runs
  the same code on the VPS, where the journal is readable.
- `--all` shows the check lines; the TUI folds them and toggles with `A`.

### Out

- Fixture (demo) scenarios keep their stored rows; no folding there.
- Deploy start/finish events from CD: install_release.sh does not write to the
  journal. A deploy is inferred from a release change between two starts.

## Decisions

### D-1: Journal is the source, ledger stays untouched

- **Decision**: Read restarts, warnings and errors from the journal; no new
  ledger event.
- **Alternatives**: a `Restarted` ledger event (extra writes, issue says none).
- **Rationale**: the journal already holds them; issue #653 forbids a service.
- **Cost of reversal**: LOW.

### D-2: A deploy is a release change between starts

- **Decision**: a start whose `release` differs from the previous start in the
  window reads `deployed <new> (was <old>)`; otherwise `restarted · release X`.
- **Alternatives**: parse CD output (not in the journal).
- **Rationale**: the symlink flips only on deploy.
- **Cost of reversal**: LOW.

### D-3: Whitelisted fields only (ADR-008, ADR-012)

- **Decision**: warning and error rows copy `event`, `pair` (coin) and
  `error`; entry conditions contribute yes counts only.
- **Rationale**: blind surfaces must never receive a price, size or balance.
- **Cost of reversal**: LOW.

### D-4: One line per candle

- **Decision**: bar rows of one boundary and interval fold into one BAR row
  (`count` = coins); signals of that candle are named on it.
- **Rationale**: issue scope; `group_activity` is unchanged.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Restarts and deploys

- **A-1.1**: a start row names the release; a changed release reads `deployed`.

### R-2: Warnings and errors

- **A-2.1**: shown with event, coin and error class only, on every surface.

### R-3: Quiet check line

- **A-3.1**: `checked N coins · no signal` or the signal; hidden unless `--all`
  or the TUI toggle; counts are yes/no only.

### R-4: Unreadable journal

- **A-4.1**: journal rows are omitted; ledger rows and check lines remain.

## Design

See `src/hermes/application/console/journal_activity.py`,
`LedgerReadModel.activity`, `entrypoints/console.py::journal_window_lines`,
and `entrypoints/_release.py` (extracted from `version.py` so serve can log the
release without a circular import).

## Test plan

1. `tests/unit/adapters/console/test_ledger_activity_journal.py` over fixture
   journal envelopes and ledgers built through the real ledger API.
2. TUI toggle and `journal_window_lines` unit tests.
3. `bash scripts/dev/pr-preflight.sh`.

Test tiers touched: unit, integration

Changed existing assertion: `test_signals_are_quiet_rows_once_fills_exist`
(signal now folded into its check line).

## Risks

- **Journal read cost**: one `journalctl` per 30 s at most, 20k lines cap.
- **Window start**: the first start in the window cannot be classified as a
  deploy.

## Dependencies on other changes

- Issues #376, #546, PR #659 (entry-condition fields).
