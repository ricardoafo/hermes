# Change: console-positions

**SDD change**: `console-positions`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/console-positions/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

This change ships `hermes positions` (one net-money row per open shadow
position) and `hermes positions COIN` (the detail view), with `--json` and
`--utc`. It reuses the read model, fixtures, `--demo` switch and Rich renderers
from `console-read-model` (#384); the only new shared code is a JSON
serialiser beside the renderers, so the CLI, the TUI and Telegram can emit the
same fields. There is no ledger-backed read model yet, so without `--demo` the
command prints the empty-state line and a note that fills start with #353. The
main risk is money leaking on a blind surface through JSON; the serialiser
asks `pnl_visible` and a test scans blind JSON and text for every money key.

## Scope

### In

- `src/hermes/application/console/serialize.py`: `positions_to_json`,
  `position_detail_to_json` (money nested under `pnl`, omitted when blind).
- `src/hermes/entrypoints/console.py`: `positions` joins `CONSOLE_COMMANDS`;
  `run_positions` parses `[COIN] [--json] [--utc]` and prints.
- `src/hermes/entrypoints/cli.py`: dispatches `positions`, carries the
  leading `--demo` scenario to it, lists it in help.
- Tests: JSON shape, blind-surface scan (text and JSON), net = gross - fees -
  tax, empty state, command plumbing; existing snapshots already cover both
  widths for the positions and detail screens.
- `changelog.d/wave-375-console-positions.md`.

### Out

- The ledger-backed read model and the read-only ledger test (#370 and the
  `LedgerReadModel`); real rows need #353, #387, #388, #389.
- The TUI Enter binding (#377) and Telegram replies (#379).
- The tax module: the detail already prints "tax estimate: not available yet".

## Decisions

### D-1: Without `--demo` the command is empty-state, not an error

- **Decision**: with no demo scenario, `hermes positions` prints "No open
  shadow positions." plus "Fills start with #353." and exits 0; `--json`
  prints `{"positions": []}`.
- **Alternatives**: surface `LiveReadModelUnavailableError` as exit 2: it
  tells the operator the console is unbuilt, but the issue asks for the
  empty-state wording before the fill path is live, and no position can exist
  yet, so the answer is true.
- **Rationale**: the empty state is correct until fills exist; once a ledger
  read model lands the factory replaces this branch.
- **Cost of reversal**: LOW.

### D-2: JSON nests money under `pnl` and omits it when blind

- **Decision**: each position object carries coin, timeframe, side, size,
  opened_at, entry_price, stop_price; operator surfaces add `pnl` (current
  price, change %, net, stop distance, high since entry, and for the detail
  gross, fees, tax, net). Blind surfaces carry neither `pnl` nor those
  figures. Amounts are decimal strings.
- **Alternatives**: flat money keys with nulls when blind: a key present at
  all invites a consumer to render it.
- **Rationale**: issue #375 acceptance; strings keep `Decimal` exact.
- **Cost of reversal**: LOW.

### D-3: `--utc` switches the display timezone only

- **Decision**: `--utc` sets `RenderContext.tz` to UTC for text and JSON
  timestamps; the default is Europe/Madrid.
- **Rationale**: the renderers already take `tz`; no data changes.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Positions command

- **A-1.1**: `uv run hermes --demo ok-two-positions positions` exits 0 and
  prints `DEMO DATA` and a net figure per row.
- **A-1.2**: `uv run hermes positions` exits 0 and prints
  `No open shadow positions.`.
- **A-1.3**: `uv run hermes --demo ok-two-positions positions ETH` prints the
  detail; an unknown coin exits 1.

### R-2: JSON and blindness

- **A-2.1**: `--json` output parses, nests money under `pnl`, and a blind
  surface omits `pnl` and every current-price, stop-distance and P&L field.
- **A-2.2**: detail net equals gross - fees - tax in the rendered text.

## Design

```python
def positions_to_json(rows, ctx) -> dict[str, object]: ...
def position_detail_to_json(detail, ctx) -> dict[str, object]: ...
def run_positions(args, stream, *, demo_flag, env) -> int: ...
```

Commands run against the current tree while writing this design:

```text
rg -n "CONSOLE_COMMANDS" src/hermes/entrypoints -> empty frozenset in console.py, consumed by apply_demo_flag
```

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit -q`, `uv run ruff check`, `uv run ruff format
   --check src tests`, `uv run mypy src`, `uv run lint-imports`.

## Risks

- Money on a blind JSON surface: mitigated by the closed-allow-list
  `pnl_visible` and a key scan test.
- `--demo` scenario must reach the command after the global flag is stripped:
  covered by a CLI test.
