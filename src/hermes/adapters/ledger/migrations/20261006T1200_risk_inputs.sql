-- 20261006T1200_risk_inputs.sql — record the RO-01 and RO-02 risk inputs, read by no rule (#835).
--
-- spread_samples: one row per depth book the serve sampler reads, with the median of the
-- pair's samples in the hour up to it (RO-02 input). Spreads are relative ((ask - bid) / mid)
-- and stored as TEXT to keep exact Decimals (code-conventions §6). Keyed on the book's own
-- receive time, so a cached book read twice writes one row.
--
-- pair_correlations: Pearson's correlation of two pairs' daily close-to-close returns over the
-- window_days returns ending at the 1D bar that closed at as_of (RO-01 input, pre-registration
-- §5.5 "30-day"). sample_count is how many returns the value covers. One row per (pair_a,
-- pair_b, window_days, as_of): replaying a 1D bar after a restart writes nothing.
--
-- Additive: two new tables, no existing table changes, so an older release ignores them.

CREATE TABLE IF NOT EXISTS spread_samples (
    pair_base         TEXT NOT NULL,
    pair_quote        TEXT NOT NULL,
    book_time         TEXT NOT NULL,
    sampled_at        TEXT NOT NULL,
    spread            TEXT NOT NULL,
    median_spread_1h  TEXT NOT NULL,
    samples_1h        INTEGER NOT NULL,
    recorded_at       TEXT NOT NULL,
    PRIMARY KEY (pair_base, pair_quote, book_time)
);

CREATE TABLE IF NOT EXISTS pair_correlations (
    pair_a        TEXT NOT NULL,
    pair_b        TEXT NOT NULL,
    window_days   INTEGER NOT NULL,
    as_of         TEXT NOT NULL,
    correlation   REAL NOT NULL,
    sample_count  INTEGER NOT NULL,
    computed_at   TEXT NOT NULL,
    PRIMARY KEY (pair_a, pair_b, window_days, as_of)
);

CREATE INDEX IF NOT EXISTS idx_pair_correlations_as_of
    ON pair_correlations (as_of);
