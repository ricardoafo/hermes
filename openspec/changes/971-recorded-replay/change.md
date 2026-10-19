# Change: 971-recorded-replay

**SDD change**: `971-recorded-replay`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/971-recorded-replay/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Part 2 of issue #971: "Market scripts can be synthetic or replay recorded
Train-window bars from a directory (`/srv/hermes-data` on server-01); the
service refuses bars dated 2024-07-01 or later." The fake market service
now loads a `replay` script that reads recorded 4h bars through the
backtests' own Binance Vision reader. The main risk is Validation or OOS
bars reaching a scenario; a window past the Validation start is refused
before any file is opened, and the reader only sees the month folders
that overlap the window.

## Scope

### In

- `tests/support/fake_market/replay.py`: `market_from_recording`, the
  window check and `REPLAY_LIMIT`, read from the signed pre-registration.
- `market_from_spec` and the `/_fake/script` control path take
  `{"replay": {"dir", "pairs", "start", "end"}}`.
- `tests/integration/fake_market/test_fake_market_replay.py`.

### Out

- Binance order endpoints and packaging the service in the release
  bundle: later parts of issue #971.
- Replaying 1h or 1m bars: the fake serves 4h and 1d today.

## Decisions

### D-1: Read through `BinanceHistoricalAdapter`

- **Decision**: replay uses the reader the backtests use.
- **Alternatives**: a second CSV reader in the fake.
- **Rationale**: one parser and one set of row checks for recorded bars.
- **Cost of reversal**: LOW.

### D-2: Expose only the overlapping months to the reader

- **Decision**: a temporary view links only the archives of the
  `<yyyy>/<mm>` folders that overlap the window; the reader walks the view.
- **Alternatives**: read every archive and drop later bars (opens
  Validation and OOS files); filter by file name (layout-dependent).
- **Rationale**: a Validation or OOS archive in the same directory is
  never opened, not only never served.
- **Cost of reversal**: LOW.

### D-3: The limit comes from the signed pre-registration

- **Decision**: `REPLAY_LIMIT` is `PreRegistration().data_windows().validation_start`.
- **Rationale**: one source for the window dates.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Exactly the recorded bars in the window

A replay holds the recorded 4h bars that open in `[start, end)`, and a 1d
bar only for a UTC day with all six of its 4h bars.

### R-2: Nothing from the Validation start on

A window ending after 2024-07-01 00:00 UTC is refused, and a refused load
leaves the old market serving.

### R-3: Later archives stay shut

An archive in a month folder after the window is never opened.

### R-4: Clear refusals

A pair with no bars, a start off 00:00 UTC, an empty pair list, an empty
window, a missing `dir` and an archive that fails to read are each refused
with a message naming the problem.

## Design

`market_from_recording` checks the window, builds a temporary directory in
the Binance Vision layout holding symlinks to the overlapping months'
archives, streams 4h bars from it with `BinanceHistoricalAdapter`, stops at
the first bar at or after `end`, and aggregates whole days with the
scripted market's `_day_bar`. Reader errors become `ReplayError`, which the
service answers as a 400.

## Test plan

`tests/integration/fake_market/test_fake_market_replay.py` writes invented
bars as Binance Vision zips, plus an unreadable July 2024 archive whose
read would fail, and checks R-1 to R-4 in process and over the socket.

## Risks

- A recording with gaps replays with the gaps; the fake serves what was
  recorded, as the exchange did.
