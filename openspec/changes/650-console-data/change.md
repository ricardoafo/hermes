# Change: 650-console-data

**SDD change**: `650-console-data`
**Date**: 2026-10-05
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/650-console-data/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

This change adds the data and key plumbing that a new full-width console
layout will need. It does not restyle or rearrange the TUI: a separate design
track decides colours, animation and the panel arrangement. It adds a market
watch view to the read model, built from what hermes records about its own bar
checks and showing no prices. It also switches the range keys to D/W/M/Y, makes
the start line read "started 5,000 USDT (€X at ECB rate, D)", rewords the stale
backfill hint now that serve prices at boot, and names the running release on
the top line. The main risk is the blinding of the shadow test. The market
watch must never show how close a pair is to an entry, so it carries no price
at all.

## Scope

### In

- **Market watch view** (`application/console/views.py`, `read_model.py`):
  `WatchedPair` and `ConsoleReadModel.market_watch()`. Each checked
  (pair, interval) stream gets its last check time, the kind of signal that
  check produced (`entry`, `exit` or none) and the next due check.
- **Ledger read model** (`adapters/console/ledger_read_model.py`): reads the
  newest `BarProcessed` row per stream and the `SignalEmitted` row with the
  same correlation id. It opens the ledger read-only (`mode=ro`), like the
  other views.
- **Fixture read model and scenarios** (`adapters/console/fixture_read_model.py`,
  `scenarios/*.json`): every demo scenario has a `market_watch` list, which is
  empty in `empty`. The data is synthetic only.
- **Panel** (`application/console/render.py`, `entrypoints/tui.py`):
  `render_market_watch`, a plain table placed after the positions in the TUI
  and in the stacked overview. Its styling is minimal on purpose.
- **Range keys** (`entrypoints/tui.py`): D/W/M/Y replace 1/7/30(3)/Y in the
  bindings, the footer, the help screen and the usage guide.
- **Start line** (`render.py`, `portfolio_figures.py`, `views.py`): the line
  reads "started 5,000 USDT (€X at ECB rate, D)", with the real seed amount and
  the ledger's quote currency (`PortfolioSummary.quote_currency`).
- **Pending hint** (`render.py`): the hint reads "EUR pending · priced on the
  next restart, or run hermes fx backfill".
- **Release on the top line** (`entrypoints/tui.py`,
  `entrypoints/commands/tui.py`): "release <name>" is read through
  `read_release`, the reader `hermes status` uses after PR #647 (issue #649).

### Out

- The full-width layout, the empty-state wording ("Watching BTC, ETH, SOL on
  4h bars") and the progress-bar shadow test. These belong to the design track
  of issue #650.
- Any entry trigger, entry level or distance to it. These are not cleared:
  see D-1.
- The 100 and 200 column snapshot matrix of the new layout. It lands with the
  layout.

## Decisions

### D-1: The market watch shows no price and no entry trigger

- **Decision**: The view holds only hermes' own check facts: the last check,
  that check's signal kind and the next check.
- **Alternatives**: (a) The last close, the change and the Donchian upper band
  with the distance to it, read through a `DonchianEngine.upper_band`
  extraction. This was drafted, then dropped before any commit. (b) The band
  re-implemented in the console. This was rejected because it would duplicate
  the strategy's formula.
- **Rationale**: Live prices sit inside the sealed shadow-test window. Under
  ADR-012 and the experiment's blinding rules, a per-pair "how close to
  entering" display is not cleared (Code Owner ruling relayed on 2026-10-05).
  The domain is left untouched, so the strategy code and the parameter
  fingerprint (`swing_parameters()`) do not change.
- **Cost of reversal**: MEDIUM. Showing the trigger later needs a domain
  extraction, which is a behaviour-preserving refactor of `high_break`, plus a
  blinding ruling.

### D-2: A signal belongs to the check of its own processing cycle

- **Decision**: The ledger model joins `SignalEmitted` to `BarProcessed` on
  `correlation_id`, which `serve` shares across one bar's cycle.
- **Alternatives**: (a) Match on `occurred_at`. This was rejected because a 4h
  bar and a 1d bar both close at midnight, so a 4h signal would also show on
  the 1d row. (b) Read the `serve.bar.processed` journal fields (PR #659).
  This was rejected because the view must not depend on the journal; the
  ledger is the source.
- **Rationale**: The correlation id is exact and needs no strategy knowledge.
- **Cost of reversal**: LOW.

### D-3: The panel carries nothing `pnl_visible` hides

- **Decision**: `render_market_watch` renders the same rows on every surface.
- **Alternatives**: Hiding the panel on blind surfaces. This is unnecessary
  because it holds no money and no prices.
- **Rationale**: `pnl_visible` governs money and prices. The view has neither.
  Tests pin that no € sign and no digits other than clock times and
  timeframes appear.
- **Cost of reversal**: LOW.

### D-4: The TUI receives the release as a callable

- **Decision**: `HomeApp(release=...)` takes a `Callable[[], str]` and re-reads
  it on every redraw. `hermes tui` passes `read_release().name`.
- **Alternatives**: Importing `entrypoints.version` into `tui.py`. This was
  rejected because that module imports a ledger adapter, and the TUI module
  imports no adapter (test-enforced).
- **Rationale**: A deploy flips the link while the screen is open, so the next
  redraw shows the new release. Tests inject the name, so no test reads
  `/opt`.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Market watch from the ledger

- **A-1.1**: `uv run pytest tests/unit/adapters/console/test_ledger_market_watch.py`
  passes. It covers one row per stream, signals that stay with their own
  check, an empty ledger, the read-only file hash, and an unknown interval
  raising `LedgerError`.

### R-2: Market watch in fixtures and on screen

- **A-2.1**: `uv run pytest tests/unit/application/console/test_market_watch.py`
  passes. It covers every scenario, the empty scenario, scenario validation,
  the exact panel rows, the absence of prices on every surface, the blind
  surface showing the same panel, and the panel placed after positions.

### R-3: Keys D/W/M/Y

- **A-3.1**: `uv run pytest tests/unit/entrypoints/test_tui.py -k "range or key or footer"`
  passes. It covers each key's range, upper case, the old number keys, no
  duplicate binding, and the footer and help text.

### R-4: Start line and pending hint

- **A-4.1**: `uv run pytest tests/unit/application/console/test_live_portfolio_surfaces.py`
  passes, with the start line "started 5,000 USDT (€4,000.00 at ECB rate,
  1 Oct)" and the reworded hint.

### R-5: Release on the top line

- **A-5.1**: `uv run pytest tests/unit/entrypoints/test_tui.py -k release`
  passes.

## Design

```text
ConsoleReadModel.market_watch() -> tuple[WatchedPair, ...]
WatchedPair(coin, quote, timeframe, last_check_at, signal: str | None, next_check_at)

ledger:  newest BarProcessed per (base, quote, interval)
         last_check_at = occurred_at + 1 ms (the candle boundary)
         next_check_at = last_check_at + interval_duration(interval)
         signal = kind of the SignalEmitted with the same correlation_id
fixture: scenarios/*.json "market_watch": [{coin, quote, timeframe,
         last_check_at, signal, next_check_at}]
render:  render_market_watch(rows, ctx): "Market watch", then a table with
         coin | tf | last check | result (entry/exit/none) | next check
TUI:     #watch-column (Vertical) = #positions + #market, in the grid cell
         positions used to hold, so the wide arrangement is unchanged
```

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest -q`, `ruff`, `ruff format --check`, `mypy src`,
   `lint-imports` and `bash scripts/dev/pr-preflight.sh` are green.
3. The SVG and plain-text snapshot changes are the intended ones only: the
   footer keys and the new market watch panel.

Test tiers touched: unit, integration

## Risks

- **Blinding**: A future edit could add a price column "for context". The
  view type has no price field, and the no-digits test fails on any amount.
- **80x24 terminals**: The new panel pushes the activity rows below the fold
  on the stacked layout until the design track's full-width layout lands. The
  body still scrolls.
- **Correlation ids**: If serve re-evaluates a bar after a crash and the
  signal already exists, it does not re-publish the signal, so that check
  reads as "none". This is rare, and the activity feed still shows the signal.

## Dependencies on other changes

- PR #647 (merged): `read_release`, the release source.
- PR #659 (open, independent): adds journal fields. This change does not
  depend on it.

## Related

- Closes #650
- Closes #649
- Builds on #546 (PR #629), #630 (PR #638) and #636 (PR #647)
