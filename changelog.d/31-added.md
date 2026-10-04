- **Kraken failover drill** (`docs/runbook/failover-to-kraken.md`): a monthly,
  timed rehearsal of the Binance to Kraken cutover from ADR-004. New read-only
  `hermes failover-check` compares the last closed bars of every traded pair on
  both venues; `scripts/deploy/failover_drill.sh` runs it on hermes-01, restarts
  `hermes.service` under `--execute`, checks open positions survive and writes a
  per-step timing report. Closes #31.
