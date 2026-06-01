-- Drop bdshare no-trade placeholder bars.
--
-- For illiquid scrips (mostly *PBOND bonds) bdshare returns a flat daily row with
-- open=high=low=0, volume=0 and close carried forward from the prior session. These are
-- days the instrument did not trade; amarstock_csv omits them. Left in place they show as
-- zero-price candles and break OHLC charts, returns, and indicators.
--
-- Guarded to source='bdshare_historical' and zero OHLC so it only removes the placeholders.
-- The adapter now filters these at ingest, so this is a one-time cleanup of legacy rows.

DELETE FROM stock_prices
WHERE source = 'bdshare_historical'
  AND (high = 0 OR open = 0 OR low = 0);

-- NOTE: rebuild the daily aggregate after this migration (cannot run inside the
-- migration's implicit transaction):
--   CALL refresh_continuous_aggregate('daily_ohlcv', NULL, NULL);
