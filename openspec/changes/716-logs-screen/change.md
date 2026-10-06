# Change: 716-logs-screen

**SDD change**: `716-logs-screen`
**Date**: 2026-10-06
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/716-logs-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Screen 8 of the Cockpit console (design spec `docs/design/console-cockpit.md`,
Logs row, MVP1 part). The journal that `hermes logs` reads becomes a queryable
list built only from shared pieces: #729's `QueryList` (query line, facet
counts, sortable table) over rows computed from journal lines, #730's
`DetailPane` / `DetailScreen` for the selected line's JSON and its trace, and
issue #711's `HudPanel` and tokens for the frames and the volume histogram. The
shared query parser gains a severity order for `level` and the `service` and
`trace` fields, so `level>=warn service:engine coin:ETH since:7d` works here
and in any later list. The main pick is computing every figure (rows, buckets,
trace tree, follow merge) in one pure application module; the main risk is the
height budget at 80x24, where the table keeps only a few rows.

## Scope

### In

- `src/hermes/application/console/query.py`: `LEVEL_ORDER` (debug < info <
  warning < error < critical, `warn` an alias), `level_rank`; `level`
  conditions compare and sort by rank and reject unknown levels;
  `SHARED_FACETS` gains `service` and `trace`.
- `src/hermes/application/console/logs.py`: `LogLine.service` from the
  journal's systemd unit (`service_name`: `hermes.service` is `engine`,
  `hermes-bot.service` is `bot`); `log_line_to_json` includes it when present.
- `src/hermes/application/console/log_figures.py` (new): `log_query_rows`,
  `line_coin`, `volume_buckets`, `trace_tree`, `append_new_lines`,
  `log_line_pretty_json`, `LOG_QUERY_FIELDS`, `LOG_FACETS`.
- `src/hermes/entrypoints/tui_query.py`: `QueryList.Refreshed` message after
  each filter or sort, and a `placeholder` argument.
- `src/hermes/entrypoints/tui_logs.py` (new): `LogsView`, `histogram_text`,
  `trace_text`, `line_detail`, `export_dir`, `export_line`, `LOG_WINDOWS`.
- `src/hermes/entrypoints/tui.py`: tab 8 hosts `LogsView` instead of the
  "coming next · issue #716" placeholder; Tab completes inside the query line;
  Logs footer chips; help lines; `exports` argument.
- `src/hermes/entrypoints/commands/tui.py`: passes `export_dir(env, home)`.
- `pyproject.toml`: import-linter contract `logs-screen-never-leaves-the-console`.

### Out

- Saved queries, alerts and log export of a whole result (MVP2, design spec).
- Drag to zoom the histogram (keys only in MVP1).
- Reading units other than `hermes.service` (the reader is unchanged; the
  `service` field is ready for more units).
- The `run:` facet (issue #731).
- The `L` key's quick logs panel stays as it is.

## Decisions

### D-1: one pure module computes every Logs figure

- **Decision**: `log_figures.py` turns `LogLine` values into query rows, histogram buckets, trace trees and the follow merge; the widget only draws.
- **Alternatives**: compute inside the Textual widget; extend `logs.py` (the `hermes logs` module).
- **Rationale**: the figures are tested without an app, and `logs.py` stays the CLI's parser and renderer.
- **Cost of reversal**: LOW.

### D-2: `level` is ordered inside the shared parser

- **Decision**: `query.py` knows one ordered field, `level`; conditions compare ranks, sorting uses ranks, unknown levels are a `QueryError` naming the levels.
- **Alternatives**: a per-list ordering table passed to `parse_query` / `matches`; a fork of the parser for logs.
- **Rationale**: the design spec has one query syntax and lists `level` as a shared facet; no other list uses `level` with other values. A per-list table can come when a second ordered field appears.
- **Cost of reversal**: LOW.

### D-3: service from the systemd unit

- **Decision**: `service` is the envelope's `UNIT` (systemd's own lines) or `_SYSTEMD_UNIT`, shortened: `hermes.service` → `engine`, `hermes-<x>.service` → `<x>`.
- **Alternatives**: the event's first segment (`serve`, `feed`); a structlog `component` field (not emitted today).
- **Rationale**: the spec's example is `service:engine`; units match the services the System screen lists.
- **Cost of reversal**: LOW.

### D-4: `trace` holds the six-character tail

- **Decision**: the `trace` column and field hold the correlation id's last six characters, as `hermes logs` prints it; the full id is in the JSON and the trace screen.
- **Alternatives**: the full 26-character id (too wide for 80 columns, and `trace:` would need it typed in full).
- **Rationale**: the operator already reads tails in `hermes logs`, Telegram and Slack.
- **Cost of reversal**: LOW.

### D-5: export only on the PC, detected from the ssh session

- **Decision**: `export_dir(env, home)` returns `~/hermes-exports` unless `SSH_CONNECTION`, `SSH_CLIENT` or `SSH_TTY` is set (`hermes --vps tui` runs over ssh); then `E` says export works from the PC. `C` copies through Textual's `copy_to_clipboard` (OSC 52), which reaches the PC's clipboard over ssh too.
- **Alternatives**: a flag set by `vps.py` on the remote command (changes the remote script and its tests); always export (would write on the VPS).
- **Rationale**: the file must land on the operator's PC; ssh's own variables say where the console runs.
- **Cost of reversal**: LOW.

### D-6: follow re-reads on the existing 5 s poll

- **Decision**: `F` toggles follow; the app's 5 s refresh calls `LogsView.tick()` while Logs is shown, which re-reads the last 2000 lines and appends the ones not shown (`append_new_lines`, keeping at most 5000).
- **Alternatives**: `journalctl --follow` in a worker thread.
- **Rationale**: the spec's refresh model is the 5 s poll; no long-lived subprocess or thread to stop.
- **Cost of reversal**: MEDIUM (a streaming reader is the MVP2 path).

### D-7: Logs keys live on the view, not the app

- **Decision**: `T`, `F`, `C`, `E`, `/`, `D W M Y` and Esc are `LogsView` bindings; they win over the app's `T` (theme) and `D W M Y` (range) while the Logs screen has focus, and Esc falls through to the app's back when no pane or query line needs closing.
- **Alternatives**: route every key in the app.
- **Rationale**: the app key map keeps "no key bound twice"; the spec's `T` for trace applies only on Logs.
- **Cost of reversal**: LOW.

## Requirements

### R-1: query line

- **A-1.1**: `level>=warn service:engine coin:ETH since:7d` over the fixture journal keeps exactly the ETH feed retry.
- **A-1.2**: `level` compares by severity (`level<info` keeps debug only), `warn` equals `warning`, an unknown level is rejected; a property test checks any level condition against the rank order.
- **A-1.3**: Tab in the query line completes `lev` to `level:` and stays on Logs; outside it Tab still cycles screens.

### R-2: facets, columns, histogram

- **A-2.1**: facet counts for level, service and coin are exact and follow the query.
- **A-2.2**: the table has time, level, service, event, coin, details and trace columns, newest first, sortable through the shared table.
- **A-2.3**: volume buckets split the window evenly, count each line once in its level and drop lines outside it; bars are stacked worst level at the bottom in the token colours; `D W M Y` set the window.

### R-3: line detail

- **A-3.1**: Enter shows the line's pretty JSON in a `DetailPane` at 160x45 and a `DetailScreen` (`◂ Logs › line 14:00:09`) at 80x24; the pane follows the cursor and Esc closes it.
- **A-3.2**: `C` copies the JSON; `E` writes `log-<UTC time>-<trace tail>.json` under the exports folder; without one (over `--vps`) it says export works from the PC.

### R-4: trace

- **A-4.1**: `trace_tree` holds exactly the lines of one correlation id, grouped by event namespace, with offsets from the first line and gaps between lines.
- **A-4.2**: `T` opens `◂ Logs › trace H1M9XA`; `]` moves to the next trace; back puts the cursor on that trace's newest line; a line with no trace says so.

### R-5: follow

- **A-5.1**: with `F` on, the refresh appends new lines without duplicates or reordering (property test) and trims to the newest 5000; with `F` off nothing is re-read.

### R-6: hard rule and snapshots

- **A-6.1**: import-linter keeps `log_figures` and `tui_logs` away from notifiers, the Telegram bot and the supervisor.
- **A-6.2**: SVG snapshots at 80x24 (list, trace) and 160x45 (pane) with motion off and a frozen clock.

All of R-1..R-6: `uv run pytest -q tests/unit/application/console/test_query.py tests/unit/application/console/test_log_figures.py tests/unit/entrypoints/test_tui_logs.py` and `uv run lint-imports`.

## Design

```text
src/hermes/application/console/
  query.py         + LEVEL_ORDER, level_rank; service, trace in SHARED_FACETS
  logs.py          + LogLine.service, service_name
  log_figures.py   log_query_rows, volume_buckets, trace_tree,
                   append_new_lines, log_line_pretty_json
src/hermes/entrypoints/
  tui_query.py     + QueryList.Refreshed, placeholder
  tui_logs.py      LogsView (HudPanel "Volume" + HudPanel "Logs" holding a
                   QueryList, and a DetailPane), histogram_text, trace_text,
                   export_dir, export_line
  tui.py           tab 8 = LogsView; Tab completes in QueryInput
```

`LogsView` reads the journal (2000 lines) each time screen 8 is shown. The
list posts `Refreshed` after every filter or sort; the view redraws the
histogram from those rows (one bar per column of the panel, 2, 3 or 5 rows
tall by terminal height) and the open pane. Rows map back to their `LogLine`
by identity, so free text never searches a hidden key.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.

Test tiers touched: unit, property

## Risks

- **80x24 height**: chrome, histogram, query line and three facet lines leave about three table rows; the full list is one `/` query or a taller terminal away.
- **Follow cost**: each tick runs journalctl for 2000 lines while following on screen 8; reading stops when the screen or follow is left.
- **Money figures in the journal**: the screen shows, copies and exports them to the operator only; the import-linter contract blocks any path to AI or notifications.
- **Clipboard over ssh**: OSC 52 depends on the terminal; when it is off, `C` does nothing visible beyond the toast.

## Dependencies on other changes

- Builds on issues #711, #729 and #730 (`openspec/changes/711-cockpit-theme`, `729-query-bar`, `730-drill-down`).
