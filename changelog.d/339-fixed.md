- **BalanceLow payload hash** (`src/hermes/domain/events/balance_low.py`): the event
  now runs the base `DomainEvent.__post_init__`, so `payload_sha256` is populated
  instead of empty; a guard test checks every event override calls the base.
  Closes #324.
