"""SQLite schema migrations for the ledger adapter.

Each ``NNNN_*.sql`` file is applied in lexical order by
:func:`hermes.adapters.ledger.migration_runner.apply_migrations`. A
``schema_migrations`` tracking table records which files have been applied
so re-runs are idempotent.

Reference: ADR-003 §03 (persistence schema).
"""
