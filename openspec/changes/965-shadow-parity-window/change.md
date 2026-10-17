# Change: 965-shadow-parity-window

**SDD change**: `965-shadow-parity-window`
**Date**: 2026-10-17
**Status**: Implemented
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/965-shadow-parity-window/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-021 Shadow Done rule 3 needs M-09 (P&L correlation) and M-10 (Sharpe
match) to pass at the end of the shadow window. `hermes parity window`
replays the whole window, from day 0 to now, through the backtest replay and
the serve kernel, starting flat as the counted shadow does (#723). It matches
the replay's round trips with the ones serve recorded and computes both
metrics with `application/calibration/shadow_parity.py`, blind: it prints and
writes pass, block or insufficient only. `hermes certify --shadow-parity
FILE` feeds the file to the gate out of `BACKTEST_CERTIFIED`, where two new
rows need both metrics to read pass. Main risk: a gate that certified without
the metrics, so a missing file fails both rows.

## Scope

### In

- `application/parity/check.py`: `replay_window`, the whole-window replay
  (warm-up, flat seed, one continuous `replay` call).
- `application/calibration/shadow_window.py`: round trips from fills,
  matching shadow to replay, aligned daily returns, and the two verdicts.
- `entrypoints/parity.py`: the `window` subcommand.
- `application/calibration/certification_gates.py`: `ShadowEvidence.m09` and
  `m10`, and the two shadow gate rows.
- `entrypoints/certify.py`: `--shadow-parity FILE`, hashed into the
  recorded evidence like `--funnel-evidence`.
- Unit and property tests; `changelog.d/965-added.md`.

### Out

- Any threshold change: ADR-011 D-19's values stay as `shadow_parity.py`
  has them.
- Unsealing raw values: they stay sealed until certification (ADR-012 D-06).
- A warm-up read past the exchange's 999-bar limit: a window whose 4h
  warm-up cannot be read from stored bars or the exchange exits 3.

## Decisions

### D-1: One continuous replay from day 0, flat

- **Decision**: the replay starts flat at the day-0 time, warmed up from the
  bars before it, and runs every stored bar of the window through one
  `replay` call with the serve kernel.
- **Alternatives**: chain `hermes parity check`'s one-day replays, each
  restored from the live ledger (the replay would follow the shadow's own
  positions, so M-09 would compare the shadow with itself).
- **Rationale**: M-09 and M-10 compare the live run with "a backtest replay
  of the same days" (2026-10-03 ruling). Only an independent replay can
  differ.
- **Cost of reversal**: LOW.

### D-2: A round trip is matched by pair and entry bar

- **Decision**: a round trip runs from a pair's first BUY while flat to the
  SELL that brings it back to flat; net P&L is sell value minus buy value
  minus every fee. A shadow round trip matches the replay round trip of the
  same pair entered at or less than one 4h bar before it (exactly one bar
  earlier does not match, since serve fills inside the bar after the
  replay's fill at the close). Unmatched round trips are left out of M-09.
- **Alternatives**: match by exit too (an exit that differs is exactly what
  M-09 should measure).
- **Rationale**: the same one-bar execution window as `hermes parity check`.
- **Cost of reversal**: LOW.

### D-3: Daily returns come from the 1D closes

- **Decision**: the shadow's daily equity is rebuilt from its recorded trades
  and the stored bars, marked bar by bar as serve marks them, and read at
  each 1D bar close; the replay's is its `daily_equity`. Returns are taken
  over the close times both have.
- **Alternatives**: read the ledger's portfolio marks (a second read path
  for the same numbers, and the parity reader has none).
- **Rationale**: the same daily convention as the §10 Sharpe kill, from the
  same bars the replay runs on.
- **Cost of reversal**: LOW.

### D-4: Insufficient fails the gate

- **Decision**: each new row passes only on `pass`. `insufficient` (fewer
  than 30 matched round trips, or fewer than 30 days) and a missing file fail.
- **Alternatives**: let `insufficient` through (a gate that passes on no
  evidence).
- **Rationale**: ADR-021 Done 3 says the metrics "pass". From v2 on a shadow
  ends at 30 round trips at the earliest, which is M-09's floor.
- **Cost of reversal**: LOW.

### D-5: The verdicts travel as a file, like the funnel

- **Decision**: `hermes parity window --out FILE` writes the strategy id,
  the window and the two statuses; certify reads it with `--shadow-parity`.
- **Alternatives**: certify replays the window itself (certify would read
  the exchange for the warm-up).
- **Rationale**: certify stays offline and pure apart from its store; the
  recorded evidence hash covers the file.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Blind

- **A-1.1**: neither the command's output nor its file holds a P&L, equity,
  correlation or Sharpe value.

### R-2: Fail closed

- **A-2.1**: the gate out of `BACKTEST_CERTIFIED` fails without
  `--shadow-parity`, and on `block` or `insufficient`.
- **A-2.2**: certify refuses a parity file that does not start at day 0 of
  the latest counted run or covers less than the shadow length (`--since` is
  free on `hermes parity window`, so a sub-window could otherwise be judged).

### R-3: Independent replay

- **A-3.1**: a replay whose fills are priced differently from the shadow's
  yields matched round trips with different P&L.

## Design

```text
hermes parity window [--since ISO] [--out FILE]
  since = --since | latest day-zero record of the strategy (runs/*.json)
  run = replay_window(source, since, now, warm-up, flat seed, serve kernel)
  shadow = round_trips(ledger trades in window); replay = round_trips(run.trades)
  m09 = m09_pnl_correlation(*matched_pnl(shadow, replay), seed=965)
  m10 = m10_sharpe_match(*aligned_returns(shadow_daily_equity(...), run.daily_equity))
  print "M-09 P&L correlation: <status>", "M-10 Sharpe match: <status>"
hermes certify <id> --shadow-parity FILE
  shadow gate rows: ..., shadow_m09 == pass, shadow_m10 == pass
```

## Test plan

- Round trips: entry and exit pairing, fees in the net, an open position
  left out; matching within one bar and not beyond.
- Aligned returns over the common close times only.
- `replay_window`: a window with one breakout yields its round trip, starting
  flat whatever the ledger held before day 0.
- Command: statuses only in output and file; exit codes.
- Gate: both rows fail without the file, on block and on insufficient; pass
  only on pass.

## Risks

- A 4h warm-up for a window over about 160 days cannot be read from the
  exchange in one request; the command exits 3 and says so.
