- **Tax guard runs in serve** (`src/hermes/entrypoints/serve.py`): the first 1D
  bar of each daily close evaluates the ADR-002 Decision 6 recharacterization
  guard over the ledger's trades of the last 90 days. A breach is recorded as
  one `AnomalyDetected` per daily close and alerted on the anomaly channels;
  nothing pauses or gates entries on it, and a failed evaluation is logged
  without stopping serve. Part of #1181.
