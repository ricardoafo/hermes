# Change: fix-klines-microseconds-2025

**SDD change**: `fix-klines-microseconds-2025`
**Date**: 2026-08-26
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#271)

Mirror of engram topic key `sdd/fix-klines-microseconds-2025/change`.

## Summary

`BinanceHistoricalAdapter` crashes with `OSError: [Errno 22] Invalid argument` on every Vision klines archive dated 2025-01 onwards. Root cause: Binance changed the archive format at 2025-01-01, moving `open_time` and `close_time` from 13-digit **milliseconds** to 16-digit **microseconds**. The adapter's `_from_epoch_ms()` blindly divides by 1000, producing an epoch value ~1.7e12 which overflows Windows' `datetime.fromtimestamp` limit. Fix: detect the timestamp width at parse time and dispatch to ms or µs division accordingly. Discovered while running the Phase 1 backtest driver against the 2024-07..2025-08 Validate window.

## Scope

### In

- **MOD**: `src/hermes/adapters/market_data/binance_historical.py` — extend `_from_epoch_ms()` to detect digit-count (13 = ms, 16 = µs) and dispatch to the right divisor. Anything else raises `ExchangeError` with a diagnostic message (defense against future format drift). Add `_US_PER_SECOND` and `_EPOCH_MS_DIGITS`/`_EPOCH_US_DIGITS` constants. Function name retained (`_from_epoch_ms`) because callers already import it — the docstring is updated to name both formats.
- **NEW**: two unit tests in `tests/unit/adapters/market_data/test_binance_historical.py`:
  - `test_row_to_bar_microsecond_timestamps_2025_format` — asserts 16-digit input decodes to the expected UTC wall-clock.
  - `test_row_to_bar_unexpected_timestamp_width_raises` — asserts an unexpected digit count (e.g. 11 = seconds) raises `ExchangeError`.
- **NEW**: `changelog.d/wave-fix-klines-microseconds-2025.md`.

### Out

- `agg_trades` archive format shift (if any). Verified separately — the `trade_time` field in agg_trades still uses ms as of 2026-08 sampling. Filing a follow-up if µs surfaces there too.
- Detecting/handling seconds (10 digits) or nanoseconds (19 digits). Not currently emitted by Binance; a future switch would require the same detection pattern extended.
- Backfilling / re-normalising already-ingested files. The fix is at parse time — no on-disk migration needed.

## Decisions

### D-1: Detect at parse time, not at ingest time

- **Decision**: `_from_epoch_ms` decides the divisor per row based on the trimmed string's digit count.
- **Alternatives**: (a) detect once at file open, cache per-ZIP; (b) require the ingest layer to normalise to ms before hand-off.
- **Rationale**: Per-row detection is O(len(digits)) which is ~15 chars — negligible. Per-file detection saves 15 chars of comparison per row but adds a state variable + interpretation edge cases (what if the format changes mid-file? — never observed but defensively handled). Ingest normalisation is worse: it rewrites the archive, breaking SHA verification (per the pre-reg §16 data manifest).
- **Cost of reversal**: LOW.

### D-2: Function name unchanged (`_from_epoch_ms`)

- **Decision**: keep `_from_epoch_ms` as the name; update the docstring to say "epoch timestamp (ms or µs)".
- **Alternatives**: rename to `_from_epoch_time`; keep `_from_epoch_ms` as a shim.
- **Rationale**: The function is private (`_` prefix). Grep across the codebase shows two callers, both in the same module. Renaming would just add churn. Docstring clarifies the actual behaviour.
- **Cost of reversal**: LOW — one grep + replace if we ever want the name change.

### D-3: Unknown digit counts raise, not silently guess

- **Decision**: anything outside {13, 16} raises `ExchangeError` with the offending digit count in the message.
- **Alternatives**: silently try seconds if 10 digits, guess nanoseconds at 19, etc.
- **Rationale**: Silent guessing masks format drift; loud errors let the operator detect + spec the next migration. The whitelist is trivial to extend when a real new format lands (add the constant, add the branch, add the test).
- **Cost of reversal**: LOW.

## Requirements

### R-1: 2025+ klines decode correctly

- **A-1.1**: `test_row_to_bar_microsecond_timestamps_2025_format` — 16-digit input `1735689600000000` decodes to `datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)`.
- **A-1.2**: A live run of `python scripts/backtest/run_calibration.py --pair BTCUSDT --start 2025-01-15 --end 2025-03-31` exits 0 (previously crashed on the first 2025-01 file).

### R-2: Pre-2025 klines still decode correctly

- **A-2.1**: `test_row_to_bar_timestamps_are_utc` (existing) continues to pass — 13-digit input `1704067200000` decodes to `datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)`.

### R-3: Unexpected widths surface loudly

- **A-3.1**: `test_row_to_bar_unexpected_timestamp_width_raises` — 11-digit input (`17040672000`) raises `ExchangeError` mentioning `unexpected timestamp digit-count`.

## Design

Two constants + one dispatch:

```python
_EPOCH_MS_DIGITS: Final[int] = 13
_EPOCH_US_DIGITS: Final[int] = 16


def _from_epoch_ms(ms_str: str, *, field_name: str, source: Path) -> datetime:
    stripped = ms_str.strip()
    raw = int(stripped)
    n_digits = len(stripped.lstrip("-"))
    if n_digits == _EPOCH_MS_DIGITS:
        seconds = raw / _MS_PER_SECOND
    elif n_digits == _EPOCH_US_DIGITS:
        seconds = raw / _US_PER_SECOND
    else:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: {field_name} has unexpected timestamp "
            f"digit-count {n_digits} ({stripped!r}) in {source} — expected 13 (ms) or 16 (µs)"
        )
    return datetime.fromtimestamp(seconds, tz=UTC)
```

## Test plan

1. `uv run pytest tests/unit/adapters/market_data/test_binance_historical.py -k row_to_bar` — 7 tests exit 0 (5 pre-existing + 2 new).
2. `HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_calibration.py --pair BTCUSDT --start 2025-01-15 --end 2025-03-31` — exits 0 and emits a JSON report (previously crashed).
3. `HERMES_DATA_DIR=D:/hermes-data python scripts/backtest/run_calibration.py --pair BTCUSDT --start 2020-09-01 --end 2024-06-30` — Train window still works (regression check).
4. `bash scripts/dev/pr-preflight.sh --render-body fix-klines-microseconds-2025` — all 17 checks green (SKIP_PYTEST=1 as needed).

## Risks

- **R1** — Binance further changes to nanoseconds (19 digits) or something else. Mitigation: the digit-count allowlist explicitly rejects unknown widths with a diagnostic message; operator + agent notice + spec the next migration.
- **R2** — Some archive mixes both formats (mid-file switchover). Mitigation: per-row dispatch handles this; each row decodes independently.
- **R3** — Leading zero or scientific notation in the raw string breaks the digit count. Mitigation: `int(stripped)` normalises + `len(str(int).lstrip("-"))` gives the digit count of the parsed integer, which is stable regardless of leading zeros.

## Dependencies on other changes

- No hard dependency. Complements PR #270 (backtest driver Phase 1) — this fix unblocks the driver's Validate-window runs.
- Follow-up: run the full Validate window (2024-07..2025-08) for BTC/ETH/SOL after this lands to compare Train vs Validate signal counts.
