-- 20261005T1300_stop_marks.sql — the trailing stop of each open position, per 4h bar (#388).
--
-- A signal stores only the stop of the bar it fires on. The serve loop records the stop the
-- strategy evaluated on every bar it holds a position (entry bar, each 4h bar, exit bar), so
-- the console can show the stop history and a restart can hand the last stop back to the
-- strategy. Prices are TEXT to keep exact Decimals (code-conventions §6). One row per
-- (strategy, pair, bar close): replaying a bar after a restart writes nothing.
--
-- Additive: no existing table changes, so an older release ignores the table.

CREATE TABLE IF NOT EXISTS stop_marks (
    strategy_id       TEXT NOT NULL,
    pair_base         TEXT NOT NULL,
    pair_quote        TEXT NOT NULL,
    entry_time        TEXT NOT NULL,
    bar_close_time    TEXT NOT NULL,
    stop_price        TEXT NOT NULL,
    chandelier_level  TEXT NOT NULL,
    atr               TEXT NOT NULL,
    high_since_entry  TEXT,
    stop_moved        INTEGER,
    recorded_at       TEXT NOT NULL,
    PRIMARY KEY (strategy_id, pair_base, pair_quote, bar_close_time)
);

CREATE INDEX IF NOT EXISTS idx_stop_marks_position
    ON stop_marks (strategy_id, pair_base, pair_quote, entry_time, bar_close_time);
