-- 0003_live_bars.sql — record every closed live bar the serve loop handles (#421).
--
-- Data only: nothing in the strategy, sizing, signals or the BarProcessed
-- event reads this table. The archive check (#359) and the nightly parity
-- replay (#358) read it.
--
-- Append-only: one row per (pair, interval, open_time); the triggers refuse
-- UPDATE and DELETE so a stored bar can never be rewritten after the fact.
-- Decimals are stored as TEXT to keep exact values (code-conventions §6).

CREATE TABLE IF NOT EXISTS live_bars (
    pair_base    TEXT NOT NULL,
    pair_quote   TEXT NOT NULL,
    interval     TEXT NOT NULL,
    open_time    TEXT NOT NULL,
    close_time   TEXT NOT NULL,
    open         TEXT NOT NULL,
    high         TEXT NOT NULL,
    low          TEXT NOT NULL,
    close        TEXT NOT NULL,
    volume       TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (pair_base, pair_quote, interval, open_time)
);

CREATE TRIGGER IF NOT EXISTS live_bars_no_update
BEFORE UPDATE ON live_bars
BEGIN
    SELECT RAISE(ABORT, 'live_bars is append-only');
END;

CREATE TRIGGER IF NOT EXISTS live_bars_no_delete
BEFORE DELETE ON live_bars
BEGIN
    SELECT RAISE(ABORT, 'live_bars is append-only');
END;
