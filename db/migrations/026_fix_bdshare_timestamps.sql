-- Fix bdshare_historical daily-bar timestamps.
--
-- bdshare daily bars were stored via to_utc(..., assume_dhaka=True), which treated the
-- bar's midnight as Dhaka-local and shifted it 6h back into the PREVIOUS UTC day
-- (e.g. trading date 2026-01-25 -> 2026-01-24 18:00 UTC). amarstock_csv stores the same
-- session at midnight UTC of the trading date, so the two sources landed in different
-- daily_ohlcv buckets: one bucket then mixed two sessions, taking open from one and
-- close from the other and SUM()-ing volume across both (double count).
--
-- Canonical convention: one row per (ticker, trading-day) at midnight UTC == trading date.
-- amarstock_csv is the bulk authority; bdshare is the incremental fallback.
--
-- Remap: new_time = (the bar's Dhaka calendar date) at 00:00 UTC.
--   (time AT TIME ZONE 'Asia/Dhaka')::date  -> the trading date
--   ::timestamp AT TIME ZONE 'UTC'          -> that date at midnight UTC
-- For already-correct midnight-UTC rows this mapping is identity, so the migration is safe
-- to run on a clean DB (no-op) and idempotent.

-- 1. Preserve bdshare's value_bdt / trades into the surviving amarstock row on overlap days
--    (amarstock_csv carries NULL value_bdt).
UPDATE stock_prices a
SET value_bdt = COALESCE(a.value_bdt, b.value_bdt),
    trades    = COALESCE(a.trades,    b.trades)
FROM stock_prices b
WHERE b.source = 'bdshare_historical'
  AND a.source = 'amarstock_csv'
  AND a.ticker = b.ticker
  AND a.time   = ((b.time AT TIME ZONE 'Asia/Dhaka')::date)::timestamp AT TIME ZONE 'UTC';

-- 2. Drop bdshare rows that collide with an amarstock row on the true trading day
--    (identical OHLC; amarstock wins).
DELETE FROM stock_prices b
WHERE b.source = 'bdshare_historical'
  AND EXISTS (
        SELECT 1 FROM stock_prices a
        WHERE a.source = 'amarstock_csv'
          AND a.ticker = b.ticker
          AND a.time   = ((b.time AT TIME ZONE 'Asia/Dhaka')::date)::timestamp AT TIME ZONE 'UTC'
  );

-- 3. Re-timestamp the remaining bdshare-only rows to midnight UTC of the trading date.
--    An in-place UPDATE of `time` moves rows across TimescaleDB chunk boundaries and
--    trips the chunk time-range check constraint, so insert shifted copies then delete
--    the originals. The inserted copies are at midnight UTC, so the DELETE's
--    `time <> midnight` predicate never matches them.
INSERT INTO stock_prices
    (time, ticker, open, high, low, close, volume, trades, value_bdt,
     prev_close, change_pct, source, ingested_at, ingestion_job, quality_flag)
SELECT ((time AT TIME ZONE 'Asia/Dhaka')::date)::timestamp AT TIME ZONE 'UTC',
       ticker, open, high, low, close, volume, trades, value_bdt,
       prev_close, change_pct, source, ingested_at, ingestion_job, quality_flag
FROM stock_prices
WHERE source = 'bdshare_historical'
  AND time::time <> '00:00:00'
ON CONFLICT (time, ticker) DO NOTHING;

DELETE FROM stock_prices
WHERE source = 'bdshare_historical'
  AND time::time <> '00:00:00';

-- NOTE: after this migration, rebuild the daily aggregate (cannot run inside the
-- migration's implicit transaction):
--   CALL refresh_continuous_aggregate('daily_ohlcv', NULL, NULL);
