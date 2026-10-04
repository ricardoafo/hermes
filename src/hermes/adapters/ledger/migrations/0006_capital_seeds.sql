-- 0006_capital_seeds.sql — the shadow start capital as a quote-currency FIFO lot.
--
-- change 405-usdt-fifo (issue #405), ADR-016 item 4.
--
-- capital_seeds: one row per strategy, written once by `hermes serve` (D-1).
--   The seed opens one lot of the quote asset (lot_id); its EUR values are
--   pending until `hermes fx backfill` prices the seed date. The link lives on
--   the seed row rather than on lots, so a later rebuild of lots keeps it.
--
-- Additive: a new table only. Existing rows are untouched.

CREATE TABLE IF NOT EXISTS capital_seeds (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id    TEXT    NOT NULL UNIQUE,
    asset          TEXT    NOT NULL,
    amount         TEXT    NOT NULL,
    seeded_at      TEXT    NOT NULL,
    lot_id         TEXT    NOT NULL UNIQUE REFERENCES lots(lot_id),
    eur_rate       TEXT,
    eur_rate_date  TEXT,
    eur_source     TEXT,
    value_eur      TEXT,
    eur_status     TEXT    NOT NULL DEFAULT 'pending'
        CHECK (eur_status IN ('ok', 'pending'))
);

CREATE INDEX IF NOT EXISTS idx_capital_seeds_asset ON capital_seeds(asset);
