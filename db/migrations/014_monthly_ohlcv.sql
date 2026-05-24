-- Monthly OHLCV continuous aggregate — hierarchical CA on top of daily_ohlcv.
-- Built from daily (not weekly) to avoid bucket-alignment drift at month boundaries.

CREATE MATERIALIZED VIEW IF NOT EXISTS monthly_ohlcv
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 month', day) AS month,
    ticker,
    FIRST(open, day)            AS open,
    MAX(high)                   AS high,
    MIN(low)                    AS low,
    LAST(close, day)            AS close,
    SUM(volume)                 AS volume,
    SUM(trades)                 AS trades,
    SUM(value_bdt)              AS value_bdt
FROM daily_ohlcv
GROUP BY month, ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'monthly_ohlcv',
    start_offset      => INTERVAL '3 months',
    end_offset        => INTERVAL '1 month',
    schedule_interval => INTERVAL '1 month',
    if_not_exists     => true
);
