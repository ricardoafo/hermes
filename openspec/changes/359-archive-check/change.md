# Change: 359-archive-check

**SDD change**: `359-archive-check`
**Date**: 2026-10-03
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#359)

Mirror of engram topic key `sdd/359-archive-check/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Issue #343 validates the shape of incoming Binance bars, not their values (risk R-6). This change adds
`hermes archive-check`, run daily by a systemd timer, which compares yesterday's stored live bars (#421) with the
Binance public daily archive for every traded pair and interval and sends one alert per mismatch naming the pair,
timeframe and bar. It is read-only against the ledger and the bar store. Main risk: Binance posts daily files some
hours after midnight UTC, so an early run sees "not published"; the timer runs at 09:00 UTC.

## Scope

### In

- `domain/market_data/bar_comparison.py`: `compare_bars`, `BarMismatch`, `MismatchKind`.
- `ports/archive_bars.py`: `ArchiveBarsPort`.
- `adapters/market_data/binance_vision_daily.py`: daily zip + `.CHECKSUM` download, SHA-256 verify, parse with the
  existing archive parser.
- `adapters/ledger/live_bar_reader.py`: `mode=ro` reader of `live_bars`.
- `application/archive_check.py`: `run_archive_check`, alerts as `AnomalyDetected` (WARNING, source
  `data.archive_check`) through the anomaly routing row.
- `entrypoints/archive_check.py` and the `archive-check` CLI subcommand.
- `deploy/systemd/hermes-archive-check.{service,timer}`.

### Out

- Enabling the timer on the VPS: `systemctl enable --now hermes-archive-check.timer` after deploy, like the backup
  timer.
- Catching up days missed while the timer was off: run `hermes archive-check --date YYYY-MM-DD` by hand.

## Decisions

### D-1: Exact match by default

- **Decision**: OHLCV must be equal; `tolerance` exists in the comparison but the command uses 0.
- **Rationale**: Live REST klines and the archive both come from Binance; any difference is a data problem.
- **Cost of reversal**: LOW.

### D-2: `close_time` compared to the millisecond

- **Decision**: Truncate both close times to milliseconds before comparing.
- **Rationale**: Since 2025 the archive stores microseconds (`...59.999999`) and the live feed milliseconds
  (`...59.999`); they are the same close.
- **Cost of reversal**: LOW.

### D-3: Unpublished day is reported, not alerted

- **Decision**: A 404 on the daily file lists the stream as unpublished and exits 0 if nothing else is wrong.
- **Rationale**: Not a data problem; alerting on it would train the operator to ignore the channel.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Seeded mismatch alerts

- **A-1.1**: A changed close in a stored day yields one alert naming pair, timeframe and bar.

### R-2: Read-only

- **A-2.1**: The ledger file is byte-identical after a check; the reader cannot create a missing ledger.

### R-3: Contract test on a recorded archive fixture

- **A-3.1**: `tests/fixtures/binance_vision/BTCUSDT-4h-2025-08-01.zip` (six recorded rows) parses into six bars; a
  bad checksum, a missing checksum and an HTTP error fail loudly.

## Design

```text
src/hermes/domain/market_data/bar_comparison.py         new
src/hermes/ports/archive_bars.py                        new
src/hermes/adapters/market_data/binance_vision_daily.py new
src/hermes/adapters/ledger/live_bar_reader.py           new
src/hermes/application/archive_check.py                 new
src/hermes/entrypoints/archive_check.py                 new
src/hermes/entrypoints/cli.py                           + archive-check
deploy/systemd/hermes-archive-check.{service,timer}     new
```

Exit codes: 0 match, 1 mismatch (alerts sent), 2 usage, 3 config, ledger or archive failure.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

## Risks

- **Alert channel down.** A notifier failure raises and the unit exits non-zero, which systemd records.
- **Large backlog after downtime.** Only the previous day is checked automatically.

## Dependencies on other changes

- #421 (live bar store) must merge first; this branch builds on it.
