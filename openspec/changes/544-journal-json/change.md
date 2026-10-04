# Change: 544-journal-json

**SDD change**: `544-journal-json`
**Date**: 2026-10-04
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

## Summary

`hermes journal -h` printed argparse help followed by an ERROR line and exit 2, and `journal list`
and `journal due` had no `--json`. This change makes `-h`/`--help` work on the command and on every
subcommand (usage plus exit codes, exit 0) and adds `--json` to `list` and `due`. Nothing else
changes: `add` and `review` still write exactly as before and exit codes are unchanged.

## Scope

### In

- `-h`/`--help` on `hermes journal` and on `add`, `due`, `list`, `review`.
- `journal list --json` and `journal due --json`, one JSON document each.

### Out

- Moving ERROR lines to stderr (separate issue).
- Any change to `add` or `review` behaviour.

## Decisions

### D-1: Help is handled before parsing

- **Decision**: If `-h`/`--help` is anywhere in argv, print the help of the addressed parser and return 0 before argparse runs.
- **Alternatives**: argparse's own `-h` (raises SystemExit, trips the required-argument checks of `add`/`review`, and would need the same ERROR handling).
- **Rationale**: Matches `hermes logs`; `add -h` works without the required options.
- **Cost of reversal**: LOW.

### D-2: JSON shapes

- **Decision**: `list --json` prints `{"entries": [...]}`; `due --json` prints `{"reviewable": [...], "sealed": [...]}`. Each entry carries `type` (`decision` or `review`) plus its dataclass fields, dates in ISO form.
- **Alternatives**: a bare array (cannot carry the reviewable/sealed split).
- **Rationale**: Keeps the sealed distinction machine-readable.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Help

- **A-1.1**: `hermes journal -h` and `hermes journal <sub> -h` exit 0, mention the exit codes and print no ERROR line.

### R-2: JSON

- **A-2.1**: `hermes journal list --json` prints one JSON document holding every entry.
- **A-2.2**: `hermes journal due --json` prints one JSON document with the reviewable and sealed decisions.

## Design

`src/hermes/entrypoints/journal.py`: `_build()` returns the parser and its subparsers (all `add_help=False`, epilog with the exit codes); `_help_text(argv)` picks the parser; `_as_json` serialises an entry.

## Test plan

1. Unit tests in `tests/unit/entrypoints/test_journal.py` cover help and both JSON outputs.
2. Existing journal tests pass unmodified.
3. `bash scripts/dev/pr-preflight.sh` green.

Test tiers touched: unit

## Risks

- **Help detection by token**: a literal `-h` used as an option value (for example `--decision -h`) prints help instead of recording. Argparse would reject such a value anyway.

## Dependencies on other changes

None.
