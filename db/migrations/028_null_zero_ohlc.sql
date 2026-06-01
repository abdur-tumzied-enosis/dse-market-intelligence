-- Convert bogus zero OHLC prices to NULL.
--
-- A handful of amarstock_csv rows recorded open/high/low as 0 on real trading days
-- (volume > 0, valid close) where the source simply omitted the price. A traded price is
-- never literally 0, so 0 here means "missing" — leaving it draws candles anchored at 0
-- and skews FIRST(open)/MIN(low) in daily_ohlcv. Normalize to NULL.
--
-- close is intentionally untouched (rows with no close were skipped at load time).
-- The loader now maps 0 -> NULL for OHLC, so this is a one-time cleanup of legacy rows.

UPDATE stock_prices
SET open = NULLIF(open, 0),
    high = NULLIF(high, 0),
    low  = NULLIF(low,  0)
WHERE open = 0 OR high = 0 OR low = 0;

-- NOTE: rebuild the daily aggregate after this migration (cannot run inside the
-- migration's implicit transaction):
--   CALL refresh_continuous_aggregate('daily_ohlcv', NULL, NULL);
