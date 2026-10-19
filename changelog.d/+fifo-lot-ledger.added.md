- **FIFO lot ledger foundation** (`src/hermes/domain/lots/`,
  `src/hermes/adapters/ledger/`, `src/hermes/ports/ledger.py`): SQLite schema
  for `trades`, `lots`, `lot_consumptions`, and `events`; migration runner
  applying numbered `*.sql` files idempotently; pure FIFO selection algorithm
  (`select_lots_fifo`) with a deterministic `LotId`-lexical tie-breaker;
  `SqliteLedger` adapter grouping every row insert + event append into ONE
  transaction (invariant I-06). Partially addresses #24 (foundation only —
  event-source wiring for tax_guard, paper-trading pause on AnomalyDetected,
  and migration rollback tooling deferred to follow-up issues).
