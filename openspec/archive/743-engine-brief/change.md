# Change: 743-engine-brief

**SDD change**: `743-engine-brief`
**Date**: 2026-10-05
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#743, a slice of #365)

## Summary

The Code Owner asked for a short plain-language "What the engine did"
section in the daily and weekly briefs. It should explain how the engine is
working without touching the experiment (comment on #365, 2026-10-04).

This change adds a read-only `engine` supervisor tool. Per configured pair
it reports:

- the bars handled per interval;
- entry signals, and exit signals with the exit rule that fired;
- fills by side;
- whether a position is held.

Both briefs gain the section and cite `[engine]`. A quiet engine is
explained against the frozen entry rules only. Everything comes from facts
the engine recorded in the ledger; nothing is fetched or computed from
market data (ADR-012 D-02). The main risk is a price, size or value
leaking through the new tool. The tool's projection is on the reviewed
field allow list, and a property test checks that no fill or entry price
appears in any output.

## Scope

### In

- `SupervisorReadPort`:
  - `bars_since(since)`, giving the `BarProcessed` count per stream;
  - `fills_since(since)`, giving the pair, side and time of each
    `TradeExecuted`;
  - `SignalFact.exit_reason`.
- `SqliteSupervisorReader` implements the three.
- The `engine` tool (`hermes:read:engine`), with `EngineView`,
  `PairActivityView` and `BarCountView`. It uses the same memorization
  guard as `signals`.
- `briefing.SOURCES` gains `engine`. The daily brief becomes the "Today"
  line plus "What the engine did"; the weekly brief adds the section after
  "Signals".
- `docs/guides/vps-operations.md`: the new scope, the grant loop and one
  paragraph on the section.

### Out

- Which entry rules held on each check (`entry_*` yes/no, #656) and the
  fills the engine declined (`serve.fill.declined`). Both are in the
  journal, not the ledger. The brief says "not on the data connection yet"
  for them.
- Scheduling and posting the brief: a standing automation the Code Owner
  enables (365-supervisor-desk-briefings, Out).

## Decisions

### D-1: A new tool, not more fields on `signals`

- **Decision**: one `engine` tool with its own scope.
- **Alternatives**: extend `signals`, or have the brief read the reader
  directly.
- **Rationale**: one tool per question keeps grants and audit lines
  readable. The brief still reads only through the dispatcher
  (365-supervisor-desk-briefings D-2).
- **Cost of reversal**: LOW.

### D-2: Counts, rule names and times only

- **Decision**: the projection has pair, bar counts per interval, the
  entry count, exit rule names, buy and sell fill counts and the holding
  start time. There is no price, quantity, fee or value.
- **Rationale**: ADR-012 D-06. A fill's existence and side are already
  implied by `positions` and `signals`, so they add no outcome.
- **Cost of reversal**: LOW.

### D-3: "Bars checked" counts `BarProcessed` rows

- **Decision**: the per-stream count is the number of `BarProcessed`
  events at or after `since`, the same rows `hermes status` counts.
- **Rationale**: it is the loop's own cursor record and holds no price.
  `live_bars` would also work, but it carries OHLCV.
- **Cost of reversal**: LOW.

### D-4: The explanation has no figures

- **Decision**: a fixed sentence explains "no signal" as "no check met
  all three frozen entry rules (breakout, momentum, regime)". It names
  what is not served yet and states no number, so it can never fail the
  gate's number rule.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The tool reports the recorded facts, blind

- **A-1.1**: `tests/unit/application/supervisor/test_tools.py` passes:
  - per-pair counts, exit rules, fills and holding come from the reader's
    facts;
  - the guard refuses early history;
  - the projection fields are on the allow list;
  - no entry or fill price reaches any output.
- **A-1.2**: `tests/unit/adapters/ledger/test_supervisor_reader.py`
  passes: `bars_since` counts per stream from its bound, and `fills_since`
  returns pair, side and time only.

### R-2: The briefs carry the section and pass the gate

- **A-2.1**: `tests/unit/application/supervisor/test_briefing.py` passes:
  - the exact section text per pair;
  - the section reads unavailable without its grant;
  - both briefs pass the gate.
- **A-2.2**: `tests/unit/entrypoints/test_supervisor.py` passes: the CLI
  prints the section from a real ledger and audits six reads.

## Design

```text
gather(): SOURCES = health, signals, engine, positions, decisions, certification
          signals and engine get {"since": now - window}
engine tool -> reader.bars_since / signals_since / fills_since / open_entries
            -> EngineView(since, pairs[PairActivityView])
compose(): daily  = Today, What the engine did
           weekly = Health, Signals, What the engine did, Open positions, ...
```

## Test plan

1. All acceptance criteria pass.
2. Full unit, integration and e2e suites pass; `pr-preflight.sh` is green.

## Risks

- A price leak through a new field. The allow-list test and the property
  test cover it. Severity if missed: an ADR-012 D-06 breach before
  certification.
- Existing tests changed on purpose: the daily brief is now two items, the
  weekly brief has one more item, the CLI audits six reads instead of five
  and `SignalFact` has `exit_reason`.
