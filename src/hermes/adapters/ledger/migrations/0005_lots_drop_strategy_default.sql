-- hermes: destructive
-- 0005_lots_drop_strategy_default.sql — drop the legacy DEFAULT 'swing' on lots.strategy_id (#325).
--
-- Every insert passes the strategy id explicitly (spec 308 D-3: swing-donchian-4h), so the default
-- only invites a second spelling. SQLite cannot drop a column default in place, so lots is rebuilt
-- with identical columns and rows; existing strategy_id values are copied unchanged.
--
-- Destructive: on an existing ledger this runs only through `hermes migrate`, which snapshots first
-- (docs/guides/ledger-migrations.md). The runner wraps it in one transaction.

CREATE TABLE lots_new (
    lot_id                     TEXT    PRIMARY KEY,
    asset                      TEXT    NOT NULL,
    original_qty               TEXT    NOT NULL,
    remaining_qty              TEXT    NOT NULL,
    cost_basis_per_unit        TEXT,
    opened_at                  TEXT    NOT NULL,
    strategy_id                TEXT    NOT NULL,
    cost_basis_native_per_unit TEXT,
    native_currency            TEXT,
    trade_id                   INTEGER REFERENCES trades(id)
);

INSERT INTO lots_new (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, opened_at,
                      strategy_id, cost_basis_native_per_unit, native_currency, trade_id)
SELECT lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, opened_at,
       strategy_id, cost_basis_native_per_unit, native_currency, trade_id
FROM lots;

DROP TABLE lots;
ALTER TABLE lots_new RENAME TO lots;

CREATE INDEX IF NOT EXISTS idx_lots_asset_opened_at ON lots(asset, opened_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lots_trade_id ON lots(trade_id);
