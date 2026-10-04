- **End-to-end serve cycle tests** (`tests/e2e/test_serve_cycle.py`): the real
  `hermes serve` composition root replays twenty days of recorded Binance bars
  for BTC, ETH and SOL and is checked against the pinned signals in the
  ledger, the notifications and the per-bar journal lines, across a stop and
  restart, a missed bar, and a `hermes status --json` snapshot. They run
  weekly and on manual dispatch in the Scheduled workflow. Closes #408.
