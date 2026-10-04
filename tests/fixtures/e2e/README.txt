serve-cycle-bars.csv.gz — recorded Binance spot klines for the serve e2e tests.

Source: https://data-api.binance.vision/api/v3/klines (public, keyless),
recorded 2026-10-03.

Content: BTCUSDT, ETHUSDT and SOLUSDT, closed bars only.
  1d bars opening 2024-03-01 .. 2025-01-20
  4h bars opening 2024-11-10 .. 2025-01-20T20:00Z
2,274 rows. Columns: symbol, interval, open_time_ms, open, high, low, close,
volume, close_time_ms (Binance's inclusive close, open + interval - 1 ms).

SHA-256 of the .csv.gz:
a5ffe0206961d766ab7892a80eb6c295722fb01ebf1a41baf03fd27dde5eb3d9

Do not edit: tests/e2e/test_serve_cycle.py pins the signals of this recording.
