-- 20261004T0300_portfolio_marks.sql — one portfolio snapshot per processed bar (#387).
--
-- The console runs as a separate process and cannot read the tracker's memory, so the serve
-- loop records the strategy's portfolio after each handled bar. Values are in the strategy's
-- quote currency, stored as TEXT to keep exact Decimals (code-conventions §6). One row per
-- (strategy, bar stream, bar close): replaying a bar after a restart writes nothing.
--
-- Additive: no existing table changes.

CREATE TABLE IF NOT EXISTS portfolio_marks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL,
    pair_base       TEXT NOT NULL,
    pair_quote      TEXT NOT NULL,
    interval        TEXT NOT NULL,
    bar_close_time  TEXT NOT NULL,
    quote           TEXT NOT NULL,
    starting_cash   TEXT NOT NULL,
    cash            TEXT NOT NULL,
    market_value    TEXT NOT NULL,
    equity          TEXT NOT NULL,
    realized_pnl    TEXT NOT NULL,
    peak_equity     TEXT NOT NULL,
    positions_json  TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    UNIQUE (strategy_id, pair_base, pair_quote, interval, bar_close_time)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_marks_strategy_close
    ON portfolio_marks (strategy_id, bar_close_time);
