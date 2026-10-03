# Change: readonly-tui-dashboard

**SDD change**: `readonly-tui-dashboard`
**Date**: 2026-10-03
**Status**: Proposed
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/readonly-tui-dashboard/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

This change adds `hermes tui`, a read-only Textual home screen over the
`ConsoleReadModel` (#384), and makes the static overview the same home screen
as text (`hermes --demo <scenario>`, or plain `hermes` when
`HERMES_CONSOLE_DEMO` is set). The portfolio total in EUR is the first big
thing on the screen; the status is one dim line when all is well and grows
only when late or down. Everything is shown in EUR, including coin prices; the
stop column is a plain price plus distance in percent. The main risk is the
home screen drifting from the static text, so both are built from the same
renderers.

## Scope

### In

- `src/hermes/entrypoints/tui.py`: the Textual app (5 s refresh, range keys,
  position detail, logs placeholder, help, quit, read-only footer, stacked
  and wide layouts).
- `hermes tui` and `hermes` home screen wiring in `entrypoints/console.py`
  and `entrypoints/cli.py`.
- Operator feedback of 2026-10-03 applied to the shared renderers: no "All
  good" panel, no tax footnote on the home screen (it moves behind `?`), no
  signals, all values in EUR (views gain `eur_per_usdt`, starting capital),
  plain stop column, labelled size figures in the detail.
- Tests: pilot tests per scenario, SVG snapshots at 80x24 and 120x40.

### Out

- Write actions and the kill switch.
- The live logs screen (#376): `L` shows a placeholder.
- The ledger-backed read model: the TUI runs on the fixture read model only.
- A live pulse and ticking age on the heartbeat dot: the fixture clock is
  frozen; the ledger read model owns the live clock.

## Decisions

### D-1: One set of renderers feeds the TUI and the static home screen

- **Decision**: the TUI embeds the Rich renderables of
  `application/console/render.py`; `render_overview` is the static home screen.
- **Alternatives**: Textual-native widgets per card: two code paths to keep in
  sync, and the Telegram surface could not reuse them.
- **Rationale**: the tests and snapshots of #384 keep guarding what the TUI shows.
- **Cost of reversal**: LOW.

### D-2: Layout is a CSS grid that changes columns with width

- **Decision**: portfolio, positions, activity and detail are four grid cells;
  one column below 100 columns (detail opens as a screen), two from 100
  (portfolio and activity left, positions and detail right).
- **Alternatives**: two compositions swapped at runtime: duplicated widgets.
- **Rationale**: cell order gives the issue's stacking order and the wide split.
- **Cost of reversal**: LOW.

### D-3: Plain `hermes` is unchanged unless a demo scenario is set

- **Decision**: `hermes` with no arguments still prints the top-level help.
  With `HERMES_CONSOLE_DEMO` set (or the leading `--demo` flag) it prints the
  home screen. No live read model exists yet, so help stays the default.
- **Alternatives**: always print the home screen: breaks the documented help
  and the existing tests, and has no data without a ledger read model.
- **Rationale**: no existing behaviour changes on a host without the variable.
- **Cost of reversal**: LOW (flip once `LedgerReadModel` lands).

### D-4: EUR everywhere; USDT only as a secondary line and in JSON

- **Decision**: the views keep the quote-currency prices and carry
  `eur_per_usdt`; renderers convert. The position detail shows one dim USDT
  line, `--json` keeps the USDT fields and adds EUR ones.
- **Alternatives**: store EUR prices in the views: loses the USDT originals.
- **Rationale**: operator ruling 2026-10-03.
- **Cost of reversal**: LOW.

### D-5: Range key `3` selects the 30-day range

- **Decision**: keys `1`, `7`, `3` and `Y`; the footer reads `1 7 30 Y`.
- **Alternatives**: a key buffer for the two-character `30`: more state for one key.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Heartbeat grows only when needed

- **A-1.1**: OK shows one dim line and no other status text.
- **A-1.2**: Late shows an amber line; down shows one red row and dims every
  money number with "as of HH:MM".

### R-2: Portfolio first, in EUR

- **A-2.1**: the total value and the change for the range are the first big
  figures; range keys 1/7/30/Y change the change line and the sparkline.
- **A-2.2**: no tax explanation on the home screen; `?` shows it.
- **A-2.3**: no USDT text on the home screen or the list.

### R-3: Positions and detail

- **A-3.1**: the stop column is `€price · −x.x%`, coloured by ATR distance, with
  no ATR wording.
- **A-3.2**: Enter opens the detail (beside the list on wide terminals).

### R-4: Read-only operator surface

- **A-4.1**: the TUI renders on `Surface.OPERATOR_TUI`; DEMO DATA shows in demo.
- **A-4.2**: the TUI module imports no ledger writer.

## Design

See D-1 to D-5. Entry points: `hermes tui`, `hermes --demo SCENARIO`.

## Test plan

1. `uv run pytest tests/unit -q`, ruff, mypy, import-linter green.
2. SVG snapshots at 80x24 and 120x40 committed for review.

## Risks

- **Textual SVG snapshots** change with the Textual version; pinned by `<1`
  range only, so a bump may need `--snapshot-update`.

## Dependencies on other changes

- #384 (read model) and #375 (positions) land first; #376 replaces the logs placeholder.
