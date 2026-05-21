-- Unique index on (time, ticker) for idempotent bulk inserts.
-- TimescaleDB hypertables require the partitioning column (time) in unique constraints.
-- Enables ON CONFLICT (time, ticker) DO NOTHING in bulk loaders.
CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_prices_time_ticker_unique
    ON stock_prices (time, ticker);
