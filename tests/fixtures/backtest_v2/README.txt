backtest_v2 — committed inputs and golden outputs for
tests/unit/scripts_backtest/test_v2_golden.py (issue #1119).

v2-golden-bars.csv.gz — SYNTHETIC bars, not market data.
  BTCUSDT 4h and 1d bars, opening 2020-03-01 .. 2020-10-09T20:00Z, 1,561 rows.
  Made by _walk(_BTC, seed=1) in tests/unit/scripts_backtest/
  test_run_portfolio_backtest.py (a seeded, upward-drifting 4h random walk and
  its daily bars), keeping the bars that close before 2020-10-10, written
  gzip with mtime 0. Columns: symbol, interval, open_time_ms, open, high, low,
  close, volume, close_time_ms (open + interval - 1 ms).
  The 200 daily bars before the 2020-09-20 .. 2020-10-10 window warm up the
  1D EMA(200); the window holds two closed v2 round trips.

  SHA-256 of the .csv.gz:
  eb55294b7f67e9c1aeaf1b9798ce037263f11f91ac0f9fcb5f0bf899989db08f

__snapshots__/v2-golden-trial-line.jsonl, __snapshots__/v2-golden-report.json
(kept under __snapshots__ so the whitespace hooks leave their bytes alone) — what
run_portfolio_backtest.py --strategy swing-mvp1-v2 writes on those bars, with
the run-time fields (recorded_at, git_sha, git_dirty, sha256; generated_at)
replaced by "<run-time>". The bytes match the tree before the strategy factory
(the parent of 8b312b01, #1098).

Regenerate only on purpose, after a reviewed change to v2's output:
  HERMES_REGEN_GOLDEN=1 uv run pytest tests/unit/scripts_backtest/test_v2_golden.py
That run rewrites both golden files and fails; review the diff, then rerun
without the variable.
