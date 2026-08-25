-- 0002_lots_add_strategy_id.sql — add strategy_id column to lots.
--
-- spec-1-domain-aggregates: every Lot must carry its StrategyId from day 1
-- (bucket-aware invariant, SYNTHESIS §1, SA-6 finding 1).
--
-- The column is added with a DEFAULT of 'swing' so existing rows in
-- pre-existing databases survive the migration without breakage.
-- New writes always supply an explicit value via _insert_lot.
--
-- Idempotent: IF NOT EXISTS is not available for ADD COLUMN; the
-- migration_runner's schema_migrations table prevents re-runs.

ALTER TABLE lots ADD COLUMN strategy_id TEXT NOT NULL DEFAULT 'swing';
