-- 0003_trade_eur_values.sql — EUR values on every trade, fees in FIFO values.
--
-- change trade-eur-values (issue #386), ADR-002 §3, ADR-016 items 3 and 5.
--
-- trades: EUR columns filled at insert from the ECB rate, or NULL with
--   eur_status = 'pending' when the lookup failed; the backfill fills them.
--   Rows written before this migration read 'pending' and are backfilled by
--   their fill date.
-- lots / lot_consumptions: rebuilt (SQLite cannot drop NOT NULL in place) so
--   the EUR values may be NULL while the rate is pending. lots gains the
--   native-currency basis and the opening trade; lot_consumptions gains the EUR
--   parts of the gain and the selling trade. Rows of both tables keep their
--   values; legacy rows have no trade link and are never re-projected.
--
-- One transaction: a failure leaves the schema at 0002.

BEGIN;

ALTER TABLE trades ADD COLUMN eur_rate      TEXT;
ALTER TABLE trades ADD COLUMN eur_rate_date TEXT;
ALTER TABLE trades ADD COLUMN eur_source    TEXT;
ALTER TABLE trades ADD COLUMN price_eur     TEXT;
ALTER TABLE trades ADD COLUMN fee_eur       TEXT;
ALTER TABLE trades ADD COLUMN eur_status    TEXT NOT NULL DEFAULT 'pending'
    CHECK (eur_status IN ('ok', 'pending'));

CREATE INDEX IF NOT EXISTS idx_trades_eur_status ON trades(eur_status);

CREATE TABLE lots_new (
    lot_id                     TEXT    PRIMARY KEY,
    asset                      TEXT    NOT NULL,
    original_qty               TEXT    NOT NULL,
    remaining_qty              TEXT    NOT NULL,
    cost_basis_per_unit        TEXT,
    opened_at                  TEXT    NOT NULL,
    strategy_id                TEXT    NOT NULL DEFAULT 'swing',
    cost_basis_native_per_unit TEXT,
    native_currency            TEXT,
    trade_id                   INTEGER REFERENCES trades(id)
);

INSERT INTO lots_new (lot_id, asset, original_qty, remaining_qty,
                      cost_basis_per_unit, opened_at, strategy_id)
SELECT lot_id, asset, original_qty, remaining_qty,
       cost_basis_per_unit, opened_at, strategy_id
FROM lots;

DROP TABLE lots;
ALTER TABLE lots_new RENAME TO lots;

CREATE INDEX IF NOT EXISTS idx_lots_asset_opened_at ON lots(asset, opened_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lots_trade_id ON lots(trade_id);

CREATE TABLE lot_consumptions_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id              TEXT    NOT NULL REFERENCES lots(lot_id),
    consumed_qty        TEXT    NOT NULL,
    consumed_asset      TEXT    NOT NULL,
    realized_gain_eur   TEXT,
    consumed_at         TEXT    NOT NULL,
    proceeds_eur        TEXT,
    cost_eur            TEXT,
    fee_eur             TEXT,
    trade_id            INTEGER REFERENCES trades(id)
);

INSERT INTO lot_consumptions_new (id, lot_id, consumed_qty, consumed_asset,
                                  realized_gain_eur, consumed_at)
SELECT id, lot_id, consumed_qty, consumed_asset, realized_gain_eur, consumed_at
FROM lot_consumptions;

DROP TABLE lot_consumptions;
ALTER TABLE lot_consumptions_new RENAME TO lot_consumptions;

CREATE INDEX IF NOT EXISTS idx_lot_consumptions_lot_id      ON lot_consumptions(lot_id);
CREATE INDEX IF NOT EXISTS idx_lot_consumptions_consumed_at ON lot_consumptions(consumed_at);
CREATE INDEX IF NOT EXISTS idx_lot_consumptions_trade_id    ON lot_consumptions(trade_id);

COMMIT;
