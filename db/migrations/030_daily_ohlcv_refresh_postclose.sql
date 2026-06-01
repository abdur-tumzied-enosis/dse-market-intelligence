-- Move daily_ohlcv continuous-aggregate refresh to post-close.
--
-- The refresh policy job was anchored at ~03:59 UTC (09:59 Asia/Dhaka) — one
-- minute BEFORE the 10:00 Dhaka market open. So each daily run materialized the
-- prior days but never today's bar: today's converging row (UPSERTed into
-- stock_prices by job_live_prices during 04:00–08:30 UTC) only became visible in
-- daily_ohlcv on the NEXT morning's run — a ~24h lag for /stocks/{t}/prices.
--
-- DSE trades Sun–Thu 10:00–14:30 Dhaka (08:30 UTC close). eod_snapshot writes the
-- final EOD bars at 14:35 Dhaka (08:35 UTC). Anchor the refresh at 09:00 UTC
-- (15:00 Dhaka) — 25 min after EOD — so today's completed bar materializes
-- same-day. end_offset stays 1h (excludes 08:00–09:00 UTC); today's bar is
-- bucketed at 00:00 UTC, well inside the refresh window, so it is unaffected.
--
-- fixed_schedule => true keeps the 09:00 UTC anchor instead of drifting by
-- (finish + interval) each run.
DO $$
DECLARE
    _job_id bigint;
    _anchor timestamptz;
BEGIN
    SELECT job_id
    INTO _job_id
    FROM timescaledb_information.jobs
    WHERE proc_name      = 'policy_refresh_continuous_aggregate'
      AND hypertable_name = 'daily_ohlcv'
    LIMIT 1;

    IF _job_id IS NOT NULL THEN
        -- next occurrence of 09:00 UTC (today if still upcoming, else tomorrow)
        _anchor := date_trunc('day', now() AT TIME ZONE 'UTC') + INTERVAL '9 hours';
        IF _anchor <= now() THEN
            _anchor := _anchor + INTERVAL '1 day';
        END IF;

        PERFORM alter_job(
            _job_id::integer,
            schedule_interval => INTERVAL '1 day',
            next_start        => _anchor,
            fixed_schedule    => true
        );
    END IF;
END;
$$;
