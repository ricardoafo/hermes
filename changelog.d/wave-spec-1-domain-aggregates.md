### Added

- **spec-1 domain aggregates — bucket-aware `Lot` + event hash chain**
  (`src/hermes/domain/value_objects/strategy_id.py`,
  `src/hermes/domain/lots/lot.py`, `src/hermes/domain/events/base.py`,
  `src/hermes/adapters/ledger/sqlite_ledger.py`,
  `src/hermes/adapters/ledger/migrations/0002_lots_add_strategy_id.sql`,
  `openspec/changes/spec-1-domain-aggregates/`): first engineering spec
  landing behind the signed paper-trading pre-registration v1
  (`docs/experiments/swing-mvp1/pre-registration-v1.md`, PR #204).
  Delivers the two foundational domain additions every subsequent
  paper-trading spec depends on. (1) `StrategyId` value object + mandatory
  `Lot.strategy_id` field — bucket-aware aggregates from day 1 per
  SA-6 finding 1 (SYNTHESIS §1). SQLite ledger INSERT/SELECT/rehydration
  updated; migration 0002 adds the column with `DEFAULT 'swing'` for
  backward compat with pre-existing rows. (2) `DomainEvent.event_seq`
  (caller-supplied monotonic sequence, kw-only default 0) +
  `DomainEvent.payload_sha256` (auto-computed BLAKE2b-256 in
  `__post_init__` — faster than SHA-256 with equivalent security margin,
  per SA-8). Enables the event-sourcing hash chain required for
  deterministic replay. Test coverage: new `test_strategy_id.py` and
  `test_domain_event_hash.py` (determinism + differentiation properties);
  21 pre-existing Lot/event construction sites updated to pass the new
  mandatory fields. See `openspec/changes/spec-1-domain-aggregates/change.md`
  for the full decision record (D-1 StrategyId shape, D-2 hash algorithm,
  D-3 event_seq semantics).
