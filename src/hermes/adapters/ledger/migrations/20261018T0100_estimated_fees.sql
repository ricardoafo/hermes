-- 20261018T0100_estimated_fees.sql — mark trades whose fee is an estimate (#1014).
--
-- When an exchange charges a fee in a third asset whose price cannot be read, the adapter charges
-- the pre-registered taker rate instead. One row here per such trade, written in the trade's own
-- transaction, so cost basis, P&L and tax can list those fees for correction from the exchange's
-- trade history. The estimated fee itself stays in trades.fee_amount.
--
-- Additive: a new table, no existing table changes, so an older release ignores it.

CREATE TABLE IF NOT EXISTS estimated_fees (
    trade_id     INTEGER PRIMARY KEY REFERENCES trades(id)
);
