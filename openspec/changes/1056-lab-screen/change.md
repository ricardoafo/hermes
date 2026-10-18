# Change: 1056-lab-screen

**SDD change**: `1056-lab-screen`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1056-lab-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Tab 5 of the Cockpit console, Lab: a read-only list of the backtested
strategies with their parameters, brief and PASS/FAIL, as the Code Owner
approved in the Lab design (2026-10-18). Everything comes from committed files
under `docs/experiments/<experiment>/` (ADR-026 §6, interface contract §5): the
batch's `summary` and signed parameters, the pre-registration front matter, the
hypothesis cards, `trial-log.jsonl` and the per-candidate verdict records. The
verdict records carry PASS, FAIL or NA and no value, so the default render has
no metric value. The trial log's own figures show only when `show_values = true`
is set by hand in the console settings file and the session is not an AI
session (`HERMES_AI_SESSION` or `CLAUDECODE`); the reader does not even load
them otherwise. The main pick is one pure join (`application/console/lab.py`)
behind a file reader; the main risk is the renumbering of tabs 5 to 8 to 6 to 9.

## Scope

### In

- `src/hermes/application/console/lab.py` (new): `build_lab`, the dataclasses,
  `LabReader`, `values_allowed`, front matter and card parsers.
- `src/hermes/adapters/console/lab_files.py` (new): `FileLabReader` over a
  clone's `docs/experiments`, `find_experiments_root`.
- `src/hermes/entrypoints/tui_lab.py` (new): `LabView`, `lab_rows`,
  `strategy_text`, `results_text`, `view_bar`, `lab_layout`.
- `src/hermes/entrypoints/tui.py`: tab 5 Lab; Shadow test, Briefs, System and
  Logs move to 6, 7, 8, 9; keys 1-9; `lab` and `ai_session` arguments.
- `src/hermes/entrypoints/tui_settings.py`: `show_values`, kept when the theme
  is saved.
- `src/hermes/entrypoints/commands/tui.py`: builds the reader from the settings
  file and the environment.
- Captions and the Lab entry of the console catalog.

### Out

- The Drafts view and the parameter chat (MVP2, issue #967): a placeholder only.
- The text-less top bar strip (issue #1097) and the hint column and contrast in
  the panel's bottom edge (issue #1096), owned by the Defects line.
- The Shadow line view (a later design).
- Any write: the screen never edits a batch, a card or a verdict.

## Decisions

### D-1: one pure join, a file reader behind a port

- **Decision**: `build_lab` takes the experiment's files as text and returns one row per strategy id; `FileLabReader` is the only code that touches the disk.
- **Alternatives**: read inside the widget; reuse `load_batch`.
- **Rationale**: tests need no disk; `load_batch` refuses a batch that breaks a rule, but the Lab must still list a draft or a half-checked-out tree.
- **Cost of reversal**: LOW.

### D-2: values are decided before they are read

- **Decision**: the reader gets a callback (`values_allowed(show_values, env)`); when it says no, `build_lab(include_values=False)` drops the trial log's figures, so no later code can show one. `V` only hides what was read.
- **Alternatives**: read everything and hide at render time.
- **Rationale**: an AI session must never hold a result value (ADR-012 D-06); not loading them is stronger than not drawing them.
- **Cost of reversal**: LOW.

### D-3: statuses beyond the design's five

- **Decision**: `TRAIN PASS` (passed Train, Validation pending) and `NO VERDICT` (runs but no verdict record, as v1 and v2) join the design's CHAMPION, GATE PASS, FAIL · validation, FAIL · train and DRAFT.
- **Alternatives**: show those as DRAFT.
- **Rationale**: DRAFT would say "never run" for a strategy with committed trials.
- **Cost of reversal**: LOW.

### D-4: the grid reorders for the wide layout

- **Decision**: one grid holds list, strategy and results; at 160 to 219 columns the results panel moves before the strategy panel in the DOM so it takes the right column across both rows.
- **Alternatives**: three nested layouts.
- **Rationale**: one DOM for four layouts keeps the cursor and scroll state when the terminal is resized.
- **Cost of reversal**: LOW.

### D-5: the gate lists thresholds, not values

- **Decision**: thresholds come from the batch's signed `backtest_funnel` block and the pre-registration §9 numbers; each check shows its Train and Validation verdict.
- **Alternatives**: show per-check values when values are on.
- **Rationale**: verdict records carry no values (ADR-026 §6), so there is nothing committed to show; the trial log's figures appear on the runs.
- **Cost of reversal**: LOW.

## Requirements

### R-1: list

One row per strategy id (batch candidate, pre-registration with a `strategy_id`, trial line or verdict record), with family, the `S T V J` strip, status, trials, family Validation touches used of 5 and last run; a pre-registration with no runs is a DRAFT row.

### R-2: panels

The selected strategy's Strategy panel (brief, rules, hypothesis, parameters signed against as run, an amber ⚠ on a run value that is neither the signed base nor a signed neighbour) and Results panel (17 checks in fixed order with thresholds, Train and Validation verdicts, runs with their hash chain, links).

### R-3: layouts

Compact (below 100 columns): list only, Enter opens the strategy over its results under `◂ Lab › Backtests › <id>`. Standard, wide and ultra: the panels beside the list as designed.

### R-4: values

The default render contains no metric value. With `show_values` on and no AI session the runs show the trial log's figures and `V` hides and shows them. In an AI session no figure reaches any screen and `V` does nothing.

### R-5: read only

No key writes a file; `hermes tui --vps` reads the release's `docs/experiments`.

## Design

`LabFiles` (text by repo path) goes into `build_lab`, which parses front matter (`yaml.safe_load`), cards (`## ` sections), the hash-chained trial log and the `hermes.verdict/1` records, then derives the status from the verdicts. `LabView` composes the view bar, a `QueryList` (the shared filter bar and sortable table) and two `HudPanel`s in a grid whose shape the width class sets. The panels draw `strategy_text` and `results_text`, pure functions of the row and the token sheet.

## Test plan

- `tests/unit/application/console/test_lab.py`: join, statuses, mismatch, family touches, values dropped (property test), thresholds.
- `tests/unit/adapters/console/test_lab_files.py`: read-only, missing root, templates skipped, callback asked on each read.
- `tests/unit/entrypoints/test_tui_lab.py`: layouts, detail screen, views, values and V, the AI-session render with no figure on any row at four sizes, read-only walk, theme save keeps `show_values`, SVG snapshots at four sizes in both themes.
- Existing tests that pressed 5 to 8 press 6 to 9 (the same screens).

## Risks

- Tab renumbering changes muscle memory and every Home snapshot's tab bar; the keys 1-9 are the design's.
- A verdict record format change in the results job (#1061) would show as rows without verdicts; the reader ignores records of another schema version.

## Dependencies on other changes

- ADR-026 and the research funnel interface contract (verdict record, issue #1059 and #1061).
- Console issues #1096 and #1097 touch shared widget files and are not required.
