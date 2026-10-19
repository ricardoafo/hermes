- **Execution-quality alerts in serve** (`src/hermes/entrypoints/serve.py`): each
  executed shadow fill's fee, slippage and latency feed the EWMA + CUSUM control
  charts of #362, primed at start-up from the ledger's past fills. The first fill
  of a breach episode is recorded as a `WARNING` anomaly and alerted; nothing
  pauses and no decision, size, kill rule or drift gate reads it. Part of #1181.
