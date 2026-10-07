# Change: 833-top-bar

**SDD change**: `833-top-bar`
**Date**: 2026-10-07
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/833-top-bar/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The console's top bar gets the three parts the design spec's Home row names
and the build lacked (found by the issue #711 and #712 design checks): the
mainnet state, the release version and a countdown meter. The mainnet badge
reads `allow_mainnet` from the config the way `hermes serve` loads it and is
never hardcoded: `SHADOW · mainnet off`, `LIVE · mainnet ON` reversed in the
down colour, or an amber `mainnet ?` when the config cannot be read. The
version line is the console half of issue #815: the package version in its
release form (`v1.0.0-shadow.1`) next to the running release. The countdown
gets the shared `Meter`, filled with the share of the bar that has passed.
No new dependency; tokens only; nothing animates. The main risk is snapshot
churn: every console picture's top rows change.

## Scope

### In

- `src/hermes/entrypoints/tui_topbar.py` (new): `mainnet_text`,
  `version_text`, `bar_elapsed_share` and the three badge words.
- `src/hermes/entrypoints/tui_widgets.py`: `window_bar_text(badge=...)` and
  `WindowBar.show_mainnet`; the badge sits before the clock and a narrow bar
  drops the clock, then cuts the status, never the badge.
- `src/hermes/entrypoints/tui.py`: `HomeApp(version=..., allow_mainnet=...)`;
  a `#mainnet` badge on the status row outside Neon; a `#countdown-meter`
  after the countdown; `_fit_release` shortens the version line, then the
  demo tag, so the heartbeat is never cut.
- `src/hermes/entrypoints/console.py`: `config_allow_mainnet(env)`.
- `src/hermes/entrypoints/commands/tui.py`: passes
  `release_label(hermes.__version__)` and `config_allow_mainnet(env)`.
- Tests: `tests/unit/entrypoints/test_tui_topbar.py` (unit and Pilot) and
  `tests/unit/entrypoints/test_tui_topbar_snapshots.py` (own snapshot dir);
  the console snapshots on main whose top rows change are regenerated.

### Out

- The status and Telegram half of issue #815 (versioning work).
- Feed latency sparkline in the top bar (design spec item 6), the run
  selector (multi-run), the breadcrumb in detail screens: other issues.
- Re-reading the config while the console runs (D-2).

## Decisions

### D-1: The badge reads the whole config, the way serve does

- **Decision**: `config_allow_mainnet(env)` calls `load_config` on the
  console's config dir (`HERMES_CONFIG_DIR` or `./config`) with the console's
  environment and returns its `allow_mainnet`; any `ConfigError` returns
  `None`, shown as `mainnet ?`.
- **Alternatives**: read only `HERMES_ALLOW_MAINNET` from the environment
  (rejected: skips the `_mainnet_only_in_prod` validator, so a flag serve
  would refuse would read as "on"); a constant "off" for shadow
  (rejected by the issue: never hardcode).
- **Rationale**: the same loader and validators as serve, so the badge can
  only say "on" when serve would accept it. The environment is passed
  explicitly (no dotenv step), like `roster_exposure_cap`; over `--vps` the
  wrapper sources `/etc/hermes/env.production` first, the file serve's unit
  reads.
- **Cost of reversal**: LOW (one function).

### D-2: Read once at start

- **Decision**: the flag is read when `hermes tui` starts, not on every poll.
- **Alternatives**: re-read every 5 s like the release link (rejected: a full
  config load on each poll, and the console's own environment cannot change
  while it runs).
- **Cost of reversal**: LOW (pass a callable instead of a value).

### D-3: Neon shows the badge in the window bar, other themes on the status row

- **Decision**: in Neon the badge sits before the clock in the window bar;
  Control room and night have no window bar, so the badge leads the status
  row.
- **Rationale**: the spec puts it in the top bar; it must be on screen in
  every theme.
- **Cost of reversal**: LOW.

### D-4: The meter's span is last check to next check

- **Decision**: fill = (now - last check) / (next check - last check),
  clamped to [0, 1]; nothing while late, down or unknown.
- **Alternatives**: a fixed 4 h (rejected: a strategy interval hardcoded in
  the UI).
- **Cost of reversal**: LOW.

### D-5: What gives way on a narrow status row

- **Decision**: the heartbeat and the badge are never cut. With a version
  the line drops the release, then itself; then the demo tag drops its
  scenario name. The meter shows only when it fits after the countdown and
  the full chip; the countdown keeps its existing rule (to the footer).
- **Rationale**: the issue #711 rule that the heartbeat is never cut, and
  the issue's "at compact width, drop the meter and keep the text".
- **Cost of reversal**: LOW.

### D-6: An app built without a flag shows `mainnet ?`

- **Decision**: `HomeApp(allow_mainnet=None)` is the default and draws the
  amber unknown badge.
- **Rationale**: the badge is never missing and never claims "off" without
  having read it. The tests on main build apps without a flag, so their
  pictures show `mainnet ?`.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The mainnet badge follows the config

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_tui_topbar.py -k "mainnet or config"` passes.

### R-2: The version shows in its release form next to the release

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_tui_topbar.py -k "version or release_label"` passes.

### R-3: The countdown meter fills with the share of the bar and drops at compact width

- **A-3.1**: `uv run pytest tests/unit/entrypoints/test_tui_topbar.py -k meter` passes.

### R-4: Pictures in both themes at the four widths

- **A-4.1**: `uv run pytest tests/unit/entrypoints/test_tui_topbar_snapshots.py` passes (12 SVGs).
- **A-4.2**: `openspec/changes/833-top-bar/design-check.md` lists every difference.

## Design

```text
src/hermes/entrypoints/tui_topbar.py      new: badge, version line, bar share
src/hermes/entrypoints/tui_widgets.py     window_bar_text(badge=), WindowBar.show_mainnet
src/hermes/entrypoints/tui.py             HomeApp(version=, allow_mainnet=), #mainnet, #countdown-meter
src/hermes/entrypoints/console.py         config_allow_mainnet(env)
src/hermes/entrypoints/commands/tui.py    wiring
```

Status row, left to right: `#mainnet` (outside Neon), `#demo-tag`,
`#release`, `#heartbeat`, `#countdown`, `#countdown-meter`, `#theme-chip`.

## Test plan

1. The acceptance criteria above pass.
2. The TUI test files and `tests/e2e` pass in one pytest run.
3. `bash scripts/dev/pr-preflight.sh` is green.

Test tiers touched: unit

## Risks

- **Snapshot churn**: every console picture's top rows change (badge,
  meter). Mitigation: the regenerated files were compared row by row; only
  rows 0-2 changed.
- **Config dir on the VPS**: a console started outside the release
  directory without `HERMES_CONFIG_DIR` shows `mainnet ?`. Mitigation: the
  `--vps` wrapper exports `HERMES_CONFIG_DIR`; `?` is the honest reading.

## Dependencies on other changes

- None. Issue #815's status and Telegram half follows separately.
