# Change: 718-shadow-screen

**SDD change**: `718-shadow-screen`
**Date**: 2026-10-07
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/718-shadow-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Screen 5 of the Cockpit console (design spec `docs/design/console-cockpit.md`,
the Shadow test row). Tab 5 replaces its placeholder with a big "N / 90" over
the shared 90-day run bar and month ticks, the five certification checks
(pass, fail or pending only, with "mechanical: pass or fail stands"), the
day-0 record with its Ready-rule verdicts, the frozen pre-registered settings
and the lab notebook entries. Under
[ADR-021 §2 and §3](../../../docs/architecture/adr-021-strategy-lifecycle-ready-and-done.md)
the run since 2026-10-04 is a warm-up: day 0 is the record `hermes
shadow-start` writes (issue #797, PR #888), so until that record exists the
screen says "warm-up · not counting". The console reads the record's file
format through a read-only port and imports nothing from the command. Which
fact starts the count sits behind one switch. The main risk is the record
format drifting from the command's writer; a contract test parses what
`DayZeroRecord.to_json` writes.

## Scope

### In

- `src/hermes/application/console/shadow_screen.py` (new): the screen model
  with no I/O: the day-0 switch (`DayZeroSource`, `DAY_ZERO_SOURCE`), day N,
  warm-up days, run progress, missed days, the five checks, the notebook
  parser, the frozen settings (`swing_parameters()`), and the read-only ports
  `ShadowRunRecords`, `ShadowEvidenceSource`, `NotebookSource` with
  `ShadowSources` to carry them.
- `src/hermes/adapters/console/shadow_records.py` (new): `RunsDirectory`
  (newest `runs/<strategy>-*.json` of the strategy, runs dir = the ledger's
  directory / `runs`, overridable) and `NotebookFile`.
- `src/hermes/adapters/console/ledger_read_model.py`: `shadow_evidence(since)`
  (seed, first fill, missed days, live M-02 drifts, a recorded strategy pause).
- `src/hermes/adapters/console/fixture_read_model.py` and every
  `scenarios/*.json`: a `shadow_screen` block of synthetic data, so every demo
  scenario shows the screen (a day-0 record or a warm-up).
- `src/hermes/entrypoints/tui_shadow.py` (new): pure renderers for the hero,
  month ticks, checks, day-0 record, settings and notebook.
- `src/hermes/entrypoints/tui.py`: tab 5's panels and layout; the shimmer
  runs on every big number.
- `src/hermes/entrypoints/tui_home.py`: `run_bar`, the one 90-day bar both the
  bottom line and the hero draw.
- `src/hermes/entrypoints/tui_widgets.py`: `BigNumber(counting=...)` so the
  count-up can format a day count; `tui_charts.py`: a one-cell `╱` glyph.
- `src/hermes/entrypoints/console.py` + `commands/tui.py`: `shadow_sources`,
  `roster_strategy_id`, `lab_notebook_path`.

### Out

- `hermes shadow-start` itself and the warm-up ledger archive: issue #797.
- A recorded kill-trigger check verdict from `hermes serve`: issue #796 (the
  check stays pending and says so).
- The static `hermes` overview (CLI) still prints the seed-based
  `shadow_test()` line.
- Run selector and per-run scoping: issue #731.

## Decisions

### D-1: day 0 behind one switch

- **Decision**: `DAY_ZERO_SOURCE` in `shadow_screen.py` names the fact that
  starts the 90 days: `SHADOW_START_RECORD` (default), `FIRST_FILL` or
  `SEED_DATE`. `day_zero()` is the only reader.
- **Alternatives**: seed date as before (ADR-021 §3 makes those days a
  warm-up); hard-coding the record (the Code Owner was still weighing the
  three options).
- **Rationale**: ADR-021 §2 ("Shadow day 0 is the first bar after every Ready
  rule holds") and §3 (day 0 recorded, issue #797). One line changes the pick.
- **Cost of reversal**: LOW.

### D-2: read #797's file format, not its code

- **Decision**: a `ShadowRunRecords` port with one method, `latest_run`, and a
  `RunsDirectory` adapter that parses `run_id`, `strategy_id`, `day_zero_at`
  (timezone-aware ISO), `release` and `checks` (each `{rule, name, verdict,
  detail}`, verdict pass / fail / "confirm by hand"), ignoring the hashes and
  versions. A record that exists but cannot
  be read raises `ShadowRecordError`; the hero says so rather than showing a
  warm-up.
- **Alternatives**: import `application/strategy_protocol/day_zero.py` (PR
  #888) into the console (it would tie the console's read path to the
  command's checks and hashing); read the ledger (the record lives beside it,
  created once and never rewritten).
- **Rationale**: the console needs five fields of a file. The fixture
  `tests/fixtures/shadow_runs/*.json` is written by `DayZeroRecord.to_json`,
  and `test_the_record_hermes_shadow_start_writes_reads_back` fails if the
  writer drifts. PR #888 landed while this branch was open; its `checks`
  carry the rule number in `rule` and the text in `name`, and the parser
  follows that.
- **Cost of reversal**: LOW.

### D-3: the checks are mechanical and say no number

- **Decision**: each check is pass, fail or pending. A fail stands as soon as
  its fact is recorded (a missed day, a kill, a breach, a logged §11
  deviation). A pass stands only when it can no longer change (day 90 reached
  with nothing failed; the closing operator entry from day 90 on). M-02 is
  judged once at day 90 on the median live drift (≤ 30 bps); before that it is
  "pending · not enough fills" or "decided at day 90". No figure is shown.
- **Alternatives**: show the running median (a distance-to-threshold number,
  against the spec's hard rules).
- **Rationale**: issue #718 acceptance ("pass/fail only for the gated
  metrics"); success-criteria G8; ADR-021 §2 Done.
- **Cost of reversal**: LOW.

### D-4: one run bar

- **Decision**: `run_bar(day, length, tokens, width)` in `tui_home.py` draws
  the brand-gradient `Meter`; `run_line` and the hero both call it.
- **Rationale**: the spec's rule "never build a second variant".
- **Cost of reversal**: LOW.

### D-4b: the bottom line counts from the same day 0

- **Decision**: `run_words(screen)` and `counted_line(...)` draw the 90-day
  line on every tab from the same `ShadowScreen` as tab 5; `run_line` keeps
  only the seed-based view for its existing callers. `HomeApp` takes its
  shadow sources from the model by default (`sources_from`).
- **Rationale**: one source of truth; the coordinator's D2 ruling of 2026-10-07.
- **Cost of reversal**: LOW.

### D-5: the big number is the shared `BigNumber`

- **Decision**: the hero is a `BigNumber` with the figure `N / 90`
  (`hero-digits`, bold, never dimmed, so the 7:1 rule of issue #712 holds);
  a `counting` callable formats the count-up. `/` is drawn as a one-cell `╱`.
  Under 100 columns or 30 rows a bold `day N / 90` line replaces it, as Home
  does. The warm-up never gets the big number.
- **Cost of reversal**: LOW.

### D-6: the screen draws no money

- **Decision**: no renderer on tab 5 takes a money view; the ledger evidence
  reads only times, counts and M-02 drifts. `pnl_visible` has nothing to hide
  here, and a test checks no `€` or `$` appears on any scenario.
- **Cost of reversal**: LOW.

## Requirements

### R-1: tab 5 shows the Shadow test screen

- **A-1.1**: `test_key_5_opens_the_shadow_screen_and_esc_goes_back` passes.
- **A-1.2**: `test_each_scenario_shows_its_check_states` passes for a counting
  run, a warm-up, a failing run and a finished run.

### R-2: day 0 and day N

- **A-2.1**: `test_day_zero_follows_the_chosen_source`,
  `test_shadow_day_counts_local_days_since_day_zero` (day 0, mid-run, 90+)
  and `test_run_progress_is_monotonic_in_the_day` pass.

### R-3: the checks

- **A-3.1**: one pass, fail and pending test per check in
  `tests/unit/application/console/test_shadow_screen.py`.

### R-4: the record and the notebook

- **A-4.1**: `tests/unit/adapters/console/test_shadow_records.py` passes.

### R-5: snapshots

- **A-5.1**: 16 SVGs `shadow_{counting,warmup}_{neon,control}_{80x24,120x36,160x45,240x60}.svg`
  in `tests/unit/entrypoints/__snapshots__/test_tui_snapshots/`.

## Design

```text
application/console/shadow_screen.py   model, switch, checks, ports (no I/O)
adapters/console/shadow_records.py     RunsDirectory, NotebookFile
adapters/console/ledger_read_model.py  + shadow_evidence(since)
adapters/console/fixture_read_model.py + shadow_evidence / latest_run / notebook_entries
entrypoints/console.py                 shadow_sources(model, env, db_path)
entrypoints/tui_shadow.py              renderers
entrypoints/tui.py                     tab 5 layout + _redraw_shadow
```

Layout: the hero panel full width; below it, from 160 columns, two columns
(checks and Day 0 left; Frozen settings and Lab notebook right); under 160
the four panels stack. The notebook shows 3, 5 or 10 entries by height.

## Test plan

1. The tests named under Requirements pass, with the TUI tests and `tests/e2e`.
2. `ruff`, `ruff format`, `mypy --strict`, `lint-imports` clean.
3. `bash scripts/dev/pr-preflight.sh` green.

## Risks

- **#797's format changes before it lands.** The adapter rejects an
  unreadable record loudly; the fixture and `parse_run_record` are the one
  place to update.
- **The CLI overview disagrees with the console** while the ledger has a
  seed and no record: it still prints the seed-based line.
- **Snapshot churn** on later chrome changes, as with every screen.
