# Change: 652-deploy-notice

**SDD change**: `652-deploy-notice`
**Date**: 2026-10-05
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

## Summary

An open console keeps running the code it started with after a deploy. The
TUI now remembers the release it started on, compares it with the current one
on its existing 5 s refresh, and shows a persistent notice with R to restart
onto the new release. Main risk: a re-exec that loops or loses arguments.

## Scope

### In

- `HomeApp` notice row, R binding, `run_home` re-exec (`src/hermes/entrypoints/tui.py`).
- Unit tests with a fake release reader and an injected exec function.
- The notice in the Cockpit theme tokens (issue #711), SVG snapshots in Neon
  and Control room at the four spec sizes, and `design-check.md`.

### Out

- The release-prune guard in the install script (issue #956, split from #652).
- The System screen's MVP2 `R` action key (issue #717).

## Decisions

### D-1: Reuse the existing release reader and tick

- **Decision**: Compare `release()` (the `read_release()` name `hermes status` uses) on the 5 s `_redraw` tick.
- **Alternatives**: a watcher thread (more moving parts); inotify (not on Windows).
- **Rationale**: the tick already re-reads the release for the top line.
- **Cost of reversal**: LOW.

### D-2: Unknown or failing reads are not a change

- **Decision**: `unknown` or a raising reader shows no notice; the first known name is the baseline.
- **Alternatives**: treat unknown as changed (false alarms mid-deploy).
- **Rationale**: the link is briefly missing while a deploy flips it.
- **Cost of reversal**: LOW.

### D-3: Re-exec after the app exits

- **Decision**: R sets a flag and exits Textual; `run_home` then calls `os.execv(sys.executable, [sys.executable, *sys.argv])`. On Windows it prints a message and returns.
- **Alternatives**: exec inside the key handler (terminal left in raw mode).
- **Rationale**: Textual restores the terminal before exec; env is inherited by `execv`.
- **Cost of reversal**: LOW.

### D-4: An amber row in the theme tokens, above the screens

- **Decision**: `#notice` is one row on `$hud-panel` with bold `$hud-warn` text and a `$hud-warn` left edge, between the status row (and the down row) and the screens.
- **Alternatives**: a toast (fades while old code keeps running); a full `HudPanel` frame (three rows at 80x24); a Rich style from the token sheet (needs a repaint on every theme switch).
- **Rationale**: TCSS variables follow the theme by themselves; one row costs the least height and shows on every tab.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Notice on change

- **A-1.1**: `uv run pytest -q tests/unit/entrypoints/test_tui_deploy_notice.py` exits 0.

### R-2: R restarts with the original argv; errors are silent

- **A-2.1**: same test file covers exec argv, no exec on plain quit, Windows message, reader errors.

### R-3: Themed, on every tab, clear of the chrome

- **A-3.1**: `uv run pytest -q tests/unit/entrypoints/test_tui_snapshots.py -k notice` passes 8 snapshots.
- **A-3.2**: the deploy-notice tests cover tab 2, the help screen, the row placement and the `warn` colour.

## Design

`HomeApp._release_changed(current)` returns whether the current name differs from the
baseline; `_redraw` toggles `#notice`; `action_restart` quits when the notice is up.
`run_home(..., exec_fn=os.execv, argv=None, platform=sys.platform)`.

## Test plan

1. Acceptance criteria above pass.
2. `bash scripts/dev/pr-preflight.sh` green.

Test tiers touched: unit

## Risks

- **Restart loop**: exec starts a new process whose baseline is the new release, so no loop.
- **Entry-point argv**: `sys.argv[0]` is the script path; `python <script>` re-runs it.

## Dependencies on other changes

- None.
