-- 0001_initial_schema.sql — foundation for FIFO tax lots + event ledger.
--
-- Introduces the four tables ADR-003 §03 documents as the Phase 1 core:
--   * trades           — every executed order (BUY/SELL)
--   * lots             — FIFO tax lots opened by BUY trades
--   * lot_consumptions — SELL-driven consumption records
--   * events           — domain event audit trail (I-06)
--
-- All monetary and quantity values are TEXT to avoid float rounding
-- (SQLite has no native DECIMAL — TEXT + Python Decimal is the pattern).
-- All timestamps are ISO-8601 UTC strings (SQLite has no native TIMESTAMP).
--
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are no-ops.
-- The migration_runner still short-circuits on the schema_migrations row
-- but the SQL itself is defensive belt-and-braces.

CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id      TEXT    NOT NULL,
    pair_base           TEXT    NOT NULL,
    pair_quote          TEXT    NOT NULL,
    side                TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty_amount          TEXT    NOT NULL,
    qty_asset           TEXT    NOT NULL,
    price_amount        TEXT    NOT NULL,
    fee_amount          TEXT    NOT NULL,
    fee_currency        TEXT    NOT NULL,
    executed_at         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_correlation_id ON trades(correlation_id);
CREATE INDEX IF NOT EXISTS idx_trades_executed_at    ON trades(executed_at);

CREATE TABLE IF NOT EXISTS lots (
    lot_id              TEXT    PRIMARY KEY,
    asset               TEXT    NOT NULL,
    original_qty        TEXT    NOT NULL,
    remaining_qty       TEXT    NOT NULL,
    cost_basis_per_unit TEXT    NOT NULL,
    opened_at           TEXT    NOT NULL
);

-- Composite index matches select_lots_fifo's expected read pattern:
--   WHERE asset = ? AND remaining_qty > '0'
--   ORDER BY opened_at ASC, lot_id ASC
CREATE INDEX IF NOT EXISTS idx_lots_asset_opened_at ON lots(asset, opened_at);

CREATE TABLE IF NOT EXISTS lot_consumptions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id              TEXT    NOT NULL REFERENCES lots(lot_id),
    consumed_qty        TEXT    NOT NULL,
    consumed_asset      TEXT    NOT NULL,
    realized_gain_eur   TEXT    NOT NULL,
    consumed_at         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lot_consumptions_lot_id      ON lot_consumptions(lot_id);
CREATE INDEX IF NOT EXISTS idx_lot_consumptions_consumed_at ON lot_consumptions(consumed_at);

-- events table — the audit trail #24 was flagged as owning.
-- payload_json holds the serialized event body; event_schema_version
-- makes the upcaster chain (code-conventions §10) trivial.
CREATE TABLE IF NOT EXISTS events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type            TEXT    NOT NULL,
    event_schema_version  INTEGER NOT NULL,
    correlation_id        TEXT    NOT NULL,
    occurred_at           TEXT    NOT NULL,
    payload_json          TEXT    NOT NULL,
    recorded_at           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_correlation_id ON events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_events_event_type     ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at    ON events(occurred_at);
