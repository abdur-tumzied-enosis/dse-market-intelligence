"""
APScheduler-based job scheduler for DSE data extraction pipeline.

Jobs are stored in PostgreSQL and survive service restarts.
Market hours: Sunday–Thursday, 10:00–14:30 BD time.

Terminology:
- light jobs (< 5s) → run in scheduler directly
- heavy jobs (> 5s or I/O bursts) → enqueue to Celery
"""
from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

import pytz
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import create_engine

from extraction.market_status import get_market_status, refresh_market_status
from mgmt.cache import get_redis

logger = logging.getLogger(__name__)
BD_TZ = pytz.timezone("Asia/Dhaka")

# Retains the boot recovery task so it is not garbage-collected mid-run.
_RECOVERY_TASK: asyncio.Task[dict[str, int]] | None = None


# ── Pure helpers (unit-testable, no DB dependency) ─────────────────────

def _to_int(value: object) -> int | None:
    """Coerce to int; None for missing/NaN/unparseable (pandas may emit NaN floats)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return int(f)


def _to_float(value: object) -> float | None:
    """Coerce to float; None for missing/NaN/Inf/unparseable."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


_LIVE_UPSERT_SQL = """
    INSERT INTO stock_prices
        (time, ticker, open, high, low, close, volume, trades,
         value_bdt, prev_close, change_pct, source, quality_flag)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
    ON CONFLICT (time, ticker) DO UPDATE SET
        high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
        volume=EXCLUDED.volume, trades=EXCLUDED.trades,
        value_bdt=EXCLUDED.value_bdt, change_pct=EXCLUDED.change_pct,
        prev_close=EXCLUDED.prev_close, source=EXCLUDED.source,
        ingested_at=NOW(), quality_flag=EXCLUDED.quality_flag
"""


# Append-only raw intraday snapshot — one row PER POLL (not per day). Feeds the
# 5/10/30/60m continuous aggregates (migration 034). cum_* stored raw; the
# *_final views diff them into per-bar deltas. No ON CONFLICT — every poll is a
# distinct (time, ticker) instant.
_INTRADAY_INSERT_SQL = """
    INSERT INTO intraday_prices
        (time, ticker, ltp, cum_volume, cum_value, cum_trades, source)
    VALUES ($1,$2,$3,$4,$5,$6,$7)
"""


def _live_records_to_rows(
    records: list[dict[str, object]], bucket_time: datetime, source: str
) -> list[tuple[object, ...]]:
    """Map live_prices snapshot records to stock_prices INSERT tuples.

    One tuple per ticker. close = ltp (live feeds) or close. Skips rows with no
    ticker or no close. Computes change_pct from prev_close when the feed omits
    it. Falls back value_mn*1e6 → value_bdt. `open` is None (live feeds carry no
    open). quality_flag='live'.
    """
    rows = []
    for rec in records:
        ticker = str(rec.get("ticker") or "").strip()
        if not ticker:
            continue
        close = rec.get("ltp")
        if close is None:
            close = rec.get("close")
        close = _to_float(close)
        if close is None:
            continue

        prev_close = _to_float(rec.get("prev_close"))
        change_pct = _to_float(rec.get("change_pct"))
        if change_pct is None and prev_close not in (None, 0):
            change_pct = (close - prev_close) / prev_close * 100.0

        value_bdt = _to_float(rec.get("value_bdt"))
        if value_bdt is None:
            value_mn = _to_float(rec.get("value_mn"))
            if value_mn is not None:
                value_bdt = value_mn * 1_000_000

        rows.append((
            bucket_time,                    # $1  time (trading-day bucket)
            ticker,                         # $2  ticker
            None,                           # $3  open (not in live feeds)
            _to_float(rec.get("high")),     # $4  high
            _to_float(rec.get("low")),      # $5  low
            close,                          # $6  close (= ltp)
            _to_int(rec.get("volume")),     # $7  volume
            _to_int(rec.get("trades")),     # $8  trades
            value_bdt,                      # $9  value_bdt
            prev_close,                     # $10 prev_close
            change_pct,                     # $11 change_pct
            source,                         # $12 source
            "live",                         # $13 quality_flag
        ))
    return rows


def _live_records_to_intraday_rows(
    records: list[dict[str, object]], snapshot_time: datetime, source: str
) -> list[tuple[object, ...]]:
    """Map live_prices snapshot records to intraday_prices INSERT tuples.

    One tuple per ticker, stamped with the real snapshot instant (NOT a day
    bucket) so 5/10/30/60m time_buckets get intraday detail. ltp = ltp or close;
    skips rows with no ticker or no ltp. Cumulative volume/value/trades stored
    AS-IS (value_mn*1e6 → value_bdt fallback); the *_final views diff them.
    """
    rows = []
    for rec in records:
        ticker = str(rec.get("ticker") or "").strip()
        if not ticker:
            continue
        ltp = rec.get("ltp")
        if ltp is None:
            ltp = rec.get("close")
        ltp = _to_float(ltp)
        if ltp is None:
            continue

        cum_value = _to_float(rec.get("value_bdt"))
        if cum_value is None:
            value_mn = _to_float(rec.get("value_mn"))
            if value_mn is not None:
                cum_value = value_mn * 1_000_000

        rows.append((
            snapshot_time,                  # $1 time (real snapshot instant, UTC)
            ticker,                         # $2 ticker
            ltp,                            # $3 ltp
            _to_int(rec.get("volume")),     # $4 cum_volume (session-cumulative)
            cum_value,                      # $5 cum_value
            _to_int(rec.get("trades")),     # $6 cum_trades
            source,                         # $7 source
        ))
    return rows


def _split_known_tickers(
    rows: list[tuple[object, ...]], known: set[str]
) -> tuple[list[tuple[object, ...]], list[str]]:
    """Partition live rows into (kept, dropped-tickers) by membership in `known`.

    stock_prices.ticker is FK → companies.ticker and the live UPSERT runs as one
    executemany batch, so a single ticker absent from companies fails the whole
    batch (one bad row → zero inserts). Live feeds occasionally carry tickers the
    company seed lacks (new listings, trading resumed after a halt), so the caller
    drops the unknowns (logging them for seeding) instead of losing the snapshot.
    `dropped` is the sorted, de-duplicated list of unknown tickers.
    """
    kept = [row for row in rows if row[1] in known]
    dropped = sorted({str(row[1]) for row in rows if row[1] not in known})
    return kept, dropped


def get_scheduler(database_url: str) -> AsyncIOScheduler:
    """Create and configure APScheduler with PostgreSQL job store."""
    engine = create_engine(database_url, echo=False)

    scheduler = AsyncIOScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(
                url=database_url.replace("sqlite:///", "postgresql://"),
                engine=engine,
            )
        },
        timezone=BD_TZ,
    )
    return scheduler


# ── Helpers ────────────────────────────────────────────────────────────


async def _upsert_macro_df(pool, df) -> int:
    """Insert/update rows from a macro AdapterResult DataFrame into macro_indicators."""
    count = 0
    for _, row in df.iterrows():
        r = await pool.fetchrow(
            """
            INSERT INTO macro_indicators
                (indicator, value, unit, period, period_type, source, fetched_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (indicator, period, source) DO UPDATE
                SET value      = EXCLUDED.value,
                    fetched_at = EXCLUDED.fetched_at
            RETURNING id
            """,
            row["indicator"],
            row["value"],
            row.get("unit"),
            row["period"],
            row.get("period_type", "unknown"),
            row["source"],
            row["fetched_at"],
        )
        if r:
            count += 1
    return count


def _norm_sector(name: str) -> str:
    """Canonicalize a sector label for cross-source matching.

    DSE's sectoral_PE page writes '&' ('Food & Allied') while companies.sector
    spells it out ('Food and Allied'); both also drift in case/spacing. Lower,
    swap '&'→'and', and collapse whitespace so the two taxonomies line up."""
    return " ".join(name.lower().replace("&", "and").split())


async def _sector_enrichment(pool) -> dict[str, tuple[object, object]]:
    """Per-sector market cap + market-cap-weighted change_pct from latest bars.

    The DSE sectoral_PE page carries only median P/E, so market_cap_bdt and
    change_pct (both rendered by the Sectors page) are derived here from
    companies + the most recent stock_prices bar per ticker, keyed by the
    normalized sector name (see _norm_sector). change_pct is a market-cap-
    weighted average so large caps drive the sector move and a single tiny or
    halted ticker can't skew it; it falls back to an equal-weight average for
    sectors whose companies have no seeded caps. Sectors with no matching active
    companies miss the dict and fall back to (None, None).
    """
    recs = await pool.fetch(
        """
        WITH latest AS (
            SELECT DISTINCT ON (ticker) ticker, change_pct
            FROM stock_prices
            ORDER BY ticker, time DESC
        )
        SELECT
            lower(replace(c.sector, '&', 'and')) AS sector_raw,
            SUM(c.market_cap_bdt)                AS market_cap_bdt,
            COALESCE(
                SUM(l.change_pct * c.market_cap_bdt)
                    FILTER (WHERE l.change_pct IS NOT NULL AND c.market_cap_bdt IS NOT NULL)
                / NULLIF(SUM(c.market_cap_bdt)
                    FILTER (WHERE l.change_pct IS NOT NULL AND c.market_cap_bdt IS NOT NULL), 0),
                AVG(l.change_pct)
            ) AS change_pct
        FROM companies c
        LEFT JOIN latest l USING (ticker)
        WHERE c.is_active = true AND c.sector IS NOT NULL
        GROUP BY lower(replace(c.sector, '&', 'and'))
        """
    )
    return {
        _norm_sector(r["sector_raw"]): (r["market_cap_bdt"], r["change_pct"])
        for r in recs
    }


async def _invalidate_sector_caches() -> None:
    """Drop the /api/sectors list + detail caches so the next request rebuilds."""
    try:
        from mgmt.cache import cache_delete_pattern
        await cache_delete_pattern("cache:api:sectors:*")
    except Exception as exc:
        logger.warning("job_sector_pe: cache invalidation failed error=%s", exc)


# ── Job Functions ──────────────────────────────────────────────────────


async def _invalidate_live_caches() -> None:
    """Drop caches that depend on live prices so the next request rebuilds."""
    try:
        from mgmt.cache import cache_delete_pattern
        await cache_delete_pattern("cache:pipeline_status:*")
        await cache_delete_pattern("cache:live_prices*")
        await cache_delete_pattern("cache:live_snapshot")
        await cache_delete_pattern("cache:api:stocks:detail:*")
        await cache_delete_pattern("cache:api:market:*")
    except Exception as exc:
        logger.warning("job_live_prices: cache invalidation failed error=%s", exc)


async def job_live_prices() -> None:
    """Fetch live prices during market hours and UPSERT one row per ticker per
    trading day into stock_prices. Live feeds report cumulative volume/value, so
    appending a fresh row each run would inflate daily_ohlcv's SUM(volume); the
    per-day bucket + ON CONFLICT DO UPDATE keeps a single converging bar."""
    from db.pool import get_pool
    from extraction.base import AllAdaptersFailedError
    from extraction.jobs import job_run
    from extraction.registry import STREAMS

    logger.info("job_live_prices: starting")
    now = datetime.now(BD_TZ)
    status = (await get_market_status())["status"]
    if status != "Open":
        logger.info("job_live_prices: market not open (status=%s) — skipping write", status)
        await _invalidate_live_caches()
        return

    # Record any polling gap since the last snapshot (boot, network stall, missed
    # tick). Non-fatal; never blocks the pull.
    from extraction.recovery import maybe_record_intraday_gap
    await maybe_record_intraday_gap(now)

    async with job_run("live_price_pull", stream_name="live_prices") as ctx:
        try:
            result = await STREAMS["live_prices"].fetch()
        except AllAdaptersFailedError as exc:
            logger.error("job_live_prices: all adapters failed error=%s", exc)
            raise

        records = result.data.to_dict("records")
        ctx["records_fetched"] = len(records)

        # Bucket to 00:00 UTC of the Dhaka trading date so the row shares its
        # (time, ticker) key — and its daily_ohlcv UTC time_bucket — with the
        # historical/EOD bars (AmarStock stamps daily bars at 00:00:00 UTC).
        # During trading hours (04:00–08:30 UTC) the Dhaka date equals the UTC
        # date, so there is no day-boundary ambiguity.
        d = now.date()
        bucket_time = datetime(d.year, d.month, d.day, tzinfo=UTC)
        rows = _live_records_to_rows(records, bucket_time, result.source_name)

        # Real snapshot instant for the append-only intraday feed (drives the
        # 5/10/30/60m CAs); the day bucket above is only for the stock_prices
        # converging daily bar.
        snapshot_time = now.astimezone(UTC)
        intraday_rows = _live_records_to_intraday_rows(
            records, snapshot_time, result.source_name
        )

        kept = rows
        if rows:
            pool = await get_pool()
            known = {r["ticker"] for r in await pool.fetch("SELECT ticker FROM companies")}
            kept, dropped = _split_known_tickers(rows, known)
            if dropped:
                logger.warning(
                    "job_live_prices: dropped %d unknown ticker(s) absent from companies: %s",
                    len(dropped), ", ".join(dropped),
                )
            if kept:
                await pool.executemany(_LIVE_UPSERT_SQL, kept)
            # Same FK (intraday_prices.ticker → companies); reuse the known filter.
            intraday_kept, _ = _split_known_tickers(intraday_rows, known)
            if intraday_kept:
                await pool.executemany(_INTRADAY_INSERT_SQL, intraday_kept)
            ctx["intraday_inserted"] = len(intraday_kept)
        ctx["records_inserted"] = len(kept)
        logger.info(
            "job_live_prices: upserted=%d intraday=%d source=%s",
            len(kept), len(intraday_rows) if rows else 0, result.source_name,
        )

    await _invalidate_live_caches()
    logger.info("job_live_prices: complete")


async def _persist_index_snapshot() -> None:
    """Upsert today's DSEX/DS30/DSES values into index_daily (one row per trading
    day). Source: the market_indices stream. Non-fatal — logs and returns on any
    failure so the EOD job continues; the next trading day recovers."""
    from db.pool import get_pool
    from extraction.registry import STREAMS

    try:
        result = await STREAMS["market_indices"].fetch()
        idx = {r["index_name"]: r for r in result.data.to_dict("records")}

        def _val(name: str) -> float | None:
            row = idx.get(name)
            return _to_float(row.get("value")) if row else None

        today = datetime.now(BD_TZ).date()
        dsex, ds30, dses = _val("DSEX"), _val("DS30"), _val("DSES")
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO index_daily (date, dsex, ds30, dses, source)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (date) DO UPDATE SET
                dsex = EXCLUDED.dsex, ds30 = EXCLUDED.ds30, dses = EXCLUDED.dses,
                source = EXCLUDED.source, ingested_at = now()
            """,
            today, dsex, ds30, dses, result.source_name,
        )
        logger.info("index_snapshot: upserted date=%s dsex=%s", today, dsex)
    except Exception as exc:
        logger.warning("index_snapshot: failed error=%s", exc)


async def job_eod_snapshot() -> None:
    """End-of-day snapshot and calculations."""
    from extraction.jobs import job_run
    logger.info("job_eod_snapshot: starting")
    async with job_run("eod_snapshot"):
        await _persist_index_snapshot()
        # TODO: implement ingest_eod_snapshot()
        # TODO: update_52week_ranges()
        # TODO: update_circuit_breakers()
        # TODO: update_market_pe()
        # TODO: enqueue celery task: run_ml_inference
    logger.info("job_eod_snapshot: complete")


async def job_announcements() -> None:
    """Fetch DSE company announcements for all active tickers (daily after market close)."""
    from extraction.bulk_load.announcement_loader import bulk_load_announcements
    logger.info("job_announcements: starting")
    summary = await bulk_load_announcements()
    logger.info(
        "job_announcements: complete ok=%d failed=%d inserted=%d",
        summary["ok"], summary["failed"], summary["total_inserted"],
    )


async def job_news_scrape() -> None:
    """
    Fetch BD financial news via Google News RSS + extract DSE ticker mentions.

    Strategy:
      1. Fetch articles from GoogleNewsRSSAdapter
      2. Insert new articles (ON CONFLICT url DO NOTHING), RETURNING newly inserted ids
      3. Run Google NL API NER only on the returned (new) rows — zero API calls for duplicates
      4. UPDATE those rows with extracted tickers

    NL API free tier: 5,000 req/month. At 12h interval ~50 new articles/run
    = ~100 calls/day → ~3,000/month. Stays within free tier.
    If GOOGLE_CLOUD_API_KEY not set: articles saved with tickers=[].
    """
    from db.pool import get_pool
    from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter
    from extraction.adapters.news.ticker_extractor import TickerExtractor, load_company_map
    from extraction.base import AdapterError
    from extraction.jobs import job_run
    from mgmt.config import get_settings

    cfg = get_settings()

    async with job_run("news_scrape", stream_name="news_en") as ctx:
        pool = await get_pool()

        # ── 1. Fetch articles ──────────────────────────────────────────────
        adapter = GoogleNewsRSSAdapter()
        try:
            result = await adapter.fetch()
        except AdapterError as exc:
            logger.error("news_scrape_adapter_failed error=%s", exc)
            raise

        df = result.data
        ctx["records_fetched"] = len(df)

        if df.empty:
            logger.info("news_scrape_no_articles")
            return

        # ── 2. Insert new articles, get back IDs of rows actually inserted ─
        # Dedup on content_hash (MD5 of headline+source), NOT url.
        # Google News proxy URLs regenerate for the same article → url dedup fails.
        newly_inserted: list[dict] = []
        for _, row in df.iterrows():
            rec = await pool.fetchrow(
                """
                INSERT INTO news
                    (source, url, headline, body, language, published_at, fetched_at,
                     tickers, ingestion_job, content_hash)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text[], $9, $10)
                ON CONFLICT (content_hash) WHERE content_hash IS NOT NULL DO NOTHING
                RETURNING id, headline, body
                """,
                row["source"],
                row["url"],
                row["headline"],
                row.get("body") or None,
                row["language"],
                row["published_at"],
                row["fetched_at"],
                [],
                ctx["job_id"],
                row.get("content_hash"),
            )
            if rec:
                newly_inserted.append({"id": rec["id"], "headline": rec["headline"], "body": rec["body"] or ""})

        ctx["records_inserted"] = len(newly_inserted)
        logger.info("news_scrape_inserted inserted=%d skipped=%d", len(newly_inserted), len(df) - len(newly_inserted))

        # ── 3 + 4. NER: extract tickers for new articles only ─────────────
        if not newly_inserted or not cfg.google_cloud_api_key:
            if not cfg.google_cloud_api_key:
                logger.warning("news_scrape_no_ner_key: GOOGLE_CLOUD_API_KEY not set — tickers empty")
            return

        company_map = await load_company_map(pool)
        extractor = TickerExtractor(cfg.google_cloud_api_key, company_map)

        for article in newly_inserted:
            text = f"{article['headline']} {article['body']}"
            result = await extractor.extract(text)
            if result.tickers or result.context_orgs or result.sentiment_score is not None:
                await pool.execute(
                    """
                    UPDATE news
                    SET tickers         = $1::text[],
                        context_orgs    = $2::text[],
                        sentiment_score = $3,
                        sentiment_label = $4
                    WHERE id = $5
                    """,
                    result.tickers,
                    result.context_orgs,
                    result.sentiment_score,
                    result.sentiment_label,
                    article["id"],
                )

        logger.info("news_scrape_ner_complete articles_processed=%d", len(newly_inserted))


async def job_daily_macro() -> None:
    """Daily macro indicators at 02:00 — FX rate + policy rate check."""
    from db.pool import get_pool
    from extraction.jobs import job_run
    from extraction.registry import STREAMS

    async with job_run("daily_macro") as ctx:
        pool = await get_pool()
        total = 0
        for stream_name in ("macro_usd_bdt", "macro_policy_rate"):
            try:
                result = await STREAMS[stream_name].fetch()
                n = await _upsert_macro_df(pool, result.data)
                total += n
                logger.info("daily_macro_stream_done stream=%s upserted=%d", stream_name, n)
            except Exception as exc:
                logger.warning("daily_macro_stream_failed stream=%s error=%s", stream_name, exc)
        ctx["records_inserted"] = total


async def job_weekly_fundamentals() -> None:
    """Weekly fundamental scrape (Sunday 23:00) — multi-year EPS/NAV/PE/div for all tickers.

    ~18 min for 406 tickers at 3 concurrent, 1.5s delay.
    """
    from extraction.bulk_load.fundamentals_historical_loader import (
        bulk_load_fundamentals_historical,
    )
    from extraction.jobs import job_run
    logger.info("job_weekly_fundamentals: starting")
    async with job_run("weekly_fundamentals") as ctx:
        summary = await bulk_load_fundamentals_historical()
        ctx["records_inserted"] = summary["total_upserted"]
    logger.info(
        "job_weekly_fundamentals: complete ok=%d failed=%d upserted=%d",
        summary["ok"], summary["failed"], summary["total_upserted"],
    )


async def job_monthly() -> None:
    """Monthly macro data (1st day, 01:00) — all 5 macro streams."""
    from db.pool import get_pool
    from extraction.jobs import job_run
    from extraction.registry import STREAMS

    async with job_run("monthly") as ctx:
        pool = await get_pool()
        total = 0
        for stream_name in (
            "macro_policy_rate", "macro_cpi", "macro_usd_bdt",
            "macro_gdp", "macro_remittance",
        ):
            try:
                result = await STREAMS[stream_name].fetch()
                n = await _upsert_macro_df(pool, result.data)
                total += n
                logger.info("monthly_macro_stream_done stream=%s upserted=%d", stream_name, n)
            except Exception as exc:
                logger.warning("monthly_macro_stream_failed stream=%s error=%s", stream_name, exc)
        ctx["records_inserted"] = total


async def job_quarterly() -> None:
    """Quarterly ML retrain (Jan/Apr/Jul/Oct 1st, 03:00 BD time).

    Enqueues retrain_ml_models to the Celery ml queue. Worker handles full
    retrain (outcomes catchup → accuracy check → XGBoost + LSTM retrain → alert).
    """
    from extraction.jobs import job_run
    from extraction.tasks import retrain_ml_models
    async with job_run("quarterly"):
        retrain_ml_models.delay()
    logger.info("job_quarterly: enqueued retrain_ml_models to ml queue")


async def job_health_checks() -> None:
    """6-hourly source health checks — populates source_health + fires alerts."""
    from extraction.observability import run_health_checks
    logger.info("job_health_checks: starting")
    await run_health_checks()
    logger.info("job_health_checks: complete")


async def job_nightly_ml() -> None:
    """
    Nightly ML inference pipeline (~22:00 BD time, after EOD snapshot).

    Enqueues run_ml_inference to the Celery ml queue so CPU-bound PyTorch/XGBoost
    work runs in the worker process — not in this scheduler event loop.
    """
    from extraction.jobs import job_run
    from extraction.tasks import run_ml_inference
    async with job_run("nightly_ml"):
        run_ml_inference.delay()
    logger.info("job_nightly_ml: enqueued run_ml_inference to ml queue")


async def job_news_sentiment() -> None:
    """Score unscored news articles with Gemini Flash sentiment analysis."""
    import logging as _logging

    from db.pool import get_pool
    from extraction.jobs import job_run
    from mgmt.config import get_settings

    _log = _logging.getLogger(__name__)
    settings = get_settings()

    async with job_run("news_sentiment", stream_name="news_en"):
        pool = await get_pool()
        from chat.agent import StockAnalystAgent
        from chat.sentiment import score_new_articles
        agent = StockAnalystAgent(
            provider=settings.chat_agent_provider,
            model=settings.chat_agent_model,
        )
        n = await score_new_articles(pool, agent._base_llm, limit=200)
        _log.info("job_news_sentiment done scored=%d", n)


async def job_sector_pe() -> None:
    """Daily sector P/E snapshot (15:45 BD, after market close).

    Fetches DSE sectoral median P/E (sector_performance stream), enriches each
    sector with market cap + a market-cap-weighted change_pct from the latest
    stock_prices bars, then appends one snapshot row per sector to sector_pe.
    The /api/sectors endpoint reads the latest row per sector (DISTINCT ON), so
    this append-only snapshot powers the Sectors page + heatmap.
    """
    from db.pool import get_pool
    from extraction.base import AllAdaptersFailedError
    from extraction.jobs import job_run
    from extraction.registry import STREAMS

    logger.info("job_sector_pe: starting")
    async with job_run("sector_pe", stream_name="sector_performance") as ctx:
        try:
            result = await STREAMS["sector_performance"].fetch()
        except AllAdaptersFailedError as exc:
            logger.error("job_sector_pe: all adapters failed error=%s", exc)
            raise

        records = result.data.to_dict("records")
        ctx["records_fetched"] = len(records)
        if not records:
            logger.info("job_sector_pe: no sector rows — skipping write")
            return

        pool = await get_pool()
        enrich = await _sector_enrichment(pool)

        insert_rows = []
        n_cap = n_chg = 0
        for rec in records:
            sector = str(rec.get("sector") or "").strip()
            if not sector:
                continue
            cap, change_pct = enrich.get(_norm_sector(sector), (None, None))
            n_cap += cap is not None
            n_chg += change_pct is not None
            insert_rows.append((
                rec["fetched_at"],          # $1 fetched_at
                sector,                     # $2 sector
                rec.get("pe"),              # $3 pe (Decimal from adapter)
                change_pct,                 # $4 change_pct (equal-weight avg)
                cap,                        # $5 market_cap_bdt
                result.source_name,         # $6 source
            ))

        if insert_rows:
            await pool.executemany(
                """
                INSERT INTO sector_pe
                    (fetched_at, sector, pe, change_pct, market_cap_bdt, source)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                insert_rows,
            )
        ctx["records_inserted"] = len(insert_rows)
        logger.info(
            "job_sector_pe: inserted=%d enriched_cap=%d enriched_change=%d source=%s",
            len(insert_rows), n_cap, n_chg, result.source_name,
        )
        if insert_rows and n_cap == 0:
            logger.warning(
                "job_sector_pe: market_cap_bdt null for all %d sectors — "
                "companies.market_cap_bdt is unseeded; heatmap tiles size equally",
                len(insert_rows),
            )

    await _invalidate_sector_caches()
    logger.info("job_sector_pe: complete")


async def job_seed_companies() -> None:
    """Refresh the companies roster from DSE + enrich new tickers (daily, pre-open).

    Sources the ticker roster from dsebd.org/company_listing.php so newly listed
    companies get registered (as placeholders), then fills name/sector/category/
    market_cap for every unenriched row from displayCompany.php. Runs before the
    10:00 market open: without a seeded companies row, job_live_prices drops a
    new listing's prices (stock_prices.ticker is FK → companies)."""
    from extraction.bulk_load.enrich_companies import enrich_companies
    from extraction.bulk_load.seed_companies import seed
    from extraction.jobs import job_run
    from mgmt.config import get_settings

    cfg = get_settings()
    sync_url = (
        cfg.database_url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )
    logger.info("job_seed_companies: starting")
    async with job_run("seed_companies") as ctx:
        roster = await seed(sync_url)
        enriched = await enrich_companies()
        ctx["records_inserted"] = roster["new"]
    logger.info(
        "job_seed_companies: complete roster_new=%d roster_total=%d enriched_ok=%d enriched_failed=%d",
        roster["new"], roster["total"], enriched["ok"], enriched["failed"],
    )


async def job_market_status_open() -> None:
    """Morning poll-until-open (cron 10:00-10:15, every minute). Refresh the
    real DSE status; on the first Closed→Open transition of the day, fire
    job_live_prices immediately so the first live pull doesn't wait for the next
    live-prices tick. A per-day Redis flag guards against re-triggering."""
    rec = await refresh_market_status()
    if rec["status"] != "Open":
        return
    flag = f"market:open_triggered:{rec['session_date']}"
    redis = await get_redis()
    if await redis.get(flag):
        return
    await redis.set(flag, "1", ex=86_400)
    logger.info("job_market_status_open: market open — triggering live_prices")
    await job_live_prices()


async def job_market_status_close() -> None:
    """Afternoon poll-until-closed (cron 14:00-14:59, every minute). Refresh the
    real DSE status; once today's status reads Closed, set a per-day Redis flag
    so later fires in the window short-circuit (no extra scrapes)."""
    today = datetime.now(BD_TZ).date().isoformat()
    flag = f"market:closed_confirmed:{today}"
    redis = await get_redis()
    if await redis.get(flag):
        return
    rec = await refresh_market_status()
    if rec["status"] == "Closed":
        await redis.set(flag, "1", ex=86_400)
        logger.info("job_market_status_close: market closed confirmed")


# ── Scheduler Configuration ────────────────────────────────────────────


def configure_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Register all scheduled jobs. Timings come from mgmt.config.Settings."""
    from mgmt.config import get_settings
    cfg = get_settings()

    if cfg.pipeline_test_mode:
        _configure_test_mode(scheduler, cfg)
    else:
        _configure_production_mode(scheduler, cfg)


def _configure_production_mode(scheduler: AsyncIOScheduler, cfg: object) -> None:
    """Production schedule: real cron/interval timings."""
    scheduler.add_job(
        job_live_prices,
        trigger="cron",
        day_of_week=cfg.live_prices_market_days,
        hour=f"{cfg.live_prices_market_open_hour}-{cfg.live_prices_market_close_hour}",
        minute=cfg.live_prices_minutes,
        id="live_price_pull",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        job_market_status_open,
        trigger="cron",
        day_of_week=cfg.live_prices_market_days,
        hour=cfg.market_status_open_hour,
        minute=cfg.market_status_open_minutes,
        id="market_status_open",
        replace_existing=True,
        misfire_grace_time=120,
    )

    scheduler.add_job(
        job_market_status_close,
        trigger="cron",
        day_of_week=cfg.live_prices_market_days,
        hour=cfg.market_status_close_hour,
        minute=cfg.market_status_close_minutes,
        id="market_status_close",
        replace_existing=True,
        misfire_grace_time=120,
    )

    scheduler.add_job(
        job_eod_snapshot,
        trigger="cron",
        day_of_week=cfg.live_prices_market_days,
        hour=cfg.eod_snapshot_hour,
        minute=cfg.eod_snapshot_minute,
        id="eod_snapshot",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_announcements,
        trigger="interval",
        hours=cfg.announcements_interval_hours,
        id="dse_announcements",
        replace_existing=True,
    )

    scheduler.add_job(
        job_daily_macro,
        trigger="cron",
        hour=cfg.daily_macro_hour,
        minute=cfg.daily_macro_minute,
        id="daily_macro",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_weekly_fundamentals,
        trigger="cron",
        day_of_week=cfg.weekly_fundamentals_day,
        hour=cfg.weekly_fundamentals_hour,
        minute=cfg.weekly_fundamentals_minute,
        id="weekly_fundamentals",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_monthly,
        trigger="cron",
        day=cfg.monthly_day,
        hour=cfg.monthly_hour,
        minute=cfg.monthly_minute,
        id="monthly",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_quarterly,
        trigger="cron",
        month=cfg.quarterly_months,
        day=cfg.quarterly_day,
        hour=cfg.quarterly_hour,
        minute=cfg.quarterly_minute,
        id="quarterly_retrain",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_health_checks,
        trigger="interval",
        hours=cfg.health_check_interval_hours,
        id="health_checks",
        replace_existing=True,
    )

    scheduler.add_job(
        job_news_scrape,
        trigger="interval",
        hours=cfg.news_scrape_interval_hours,
        id="news_scrape",
        replace_existing=True,
    )

    scheduler.add_job(
        job_nightly_ml,
        trigger="cron",
        hour=22,
        minute=0,
        timezone=BD_TZ,
        id="nightly_ml",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_news_sentiment,
        "cron",
        id="news_sentiment",
        hour=3,
        minute=30,
        timezone=BD_TZ,
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_sector_pe,
        trigger="cron",
        hour=15,
        minute=45,
        timezone=BD_TZ,
        id="sector_pe",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_seed_companies,
        trigger="cron",
        hour=8,
        minute=0,
        timezone=BD_TZ,
        id="seed_companies",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    logger.info("scheduler: all 15 jobs registered (production mode)")


def _configure_test_mode(scheduler: AsyncIOScheduler, cfg: object) -> None:
    """
    Test mode: compress all intervals to minutes so a week of runs completes in ~1h.
    Simulated cadence per 60 min:
      live_prices       every 2min  → ~30 runs  (prod: ~19/market-day)
      eod_snapshot      every 5min  → ~12 runs  (prod: 1/day)
      announcements     every 5min  → ~12 runs  (prod: 12/day)
      daily_macro       every 7min  → ~8 runs   (prod: 1/day)
      weekly_fundam.    every 10min → ~6 runs   (prod: 1/week)
      monthly           every 15min → ~4 runs   (prod: 1/month)
      quarterly_retrain every 20min → ~3 runs   (prod: 1/quarter)
      health_checks     every 5min  → ~12 runs  (prod: 4/day)
    """
    job_map = [
        (job_live_prices,         "live_price_pull",     cfg.test_live_prices_minutes),
        (job_market_status_open,  "market_status_open",  cfg.test_market_status_minutes),
        (job_market_status_close, "market_status_close", cfg.test_market_status_minutes),
        (job_eod_snapshot,        "eod_snapshot",        cfg.test_eod_snapshot_minutes),
        (job_announcements,       "dse_announcements",   cfg.test_announcements_minutes),
        (job_news_scrape,         "news_scrape",         cfg.test_news_scrape_minutes),
        (job_daily_macro,         "daily_macro",         cfg.test_daily_macro_minutes),
        (job_weekly_fundamentals, "weekly_fundamentals", cfg.test_weekly_fundamentals_minutes),
        (job_monthly,             "monthly",             cfg.test_monthly_minutes),
        (job_quarterly,           "quarterly_retrain",   cfg.test_quarterly_minutes),
        (job_health_checks,       "health_checks",       cfg.test_health_check_minutes),
        (job_nightly_ml,          "nightly_ml",          cfg.test_nightly_ml_minutes),
        (job_news_sentiment,      "news_sentiment",      cfg.test_news_sentiment_minutes),
        (job_sector_pe,           "sector_pe",           cfg.test_sector_pe_minutes),
        (job_seed_companies,      "seed_companies",      cfg.test_seed_companies_minutes),
    ]
    for func, job_id, interval_minutes in job_map:
        scheduler.add_job(
            func,
            trigger="interval",
            minutes=interval_minutes,
            id=job_id,
            replace_existing=True,
        )

    logger.warning(
        "scheduler: TEST MODE — all 15 intervals compressed to minutes. "
        "Do NOT use in production."
    )


async def start_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Start the scheduler, then kick off boot-time recovery in the background."""
    import asyncio

    configure_scheduler(scheduler)
    scheduler.start()
    logger.info("scheduler: started")

    # Converge missed daily/EOD jobs + resume live polling. Run as a background
    # task so a slow catch-up never blocks scheduler startup.
    from extraction.recovery import recover_missed_jobs

    def _log_recovery_done(task: asyncio.Task[dict[str, int]]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("recovery: boot task failed error=%s", exc, exc_info=exc)
        else:
            logger.info("recovery: boot task done summary=%s", task.result())

    global _RECOVERY_TASK
    _RECOVERY_TASK = asyncio.create_task(recover_missed_jobs())
    _RECOVERY_TASK.add_done_callback(_log_recovery_done)


async def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Stop the scheduler gracefully."""
    scheduler.shutdown(wait=True)
    logger.info("scheduler: stopped")


if __name__ == "__main__":
    import asyncio
    import signal

    from mgmt.config import get_settings

    settings = get_settings()
    sync_url = (
        settings.database_url
        .replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql://", "postgresql+psycopg://")
    )

    sched = get_scheduler(sync_url)

    async def _run() -> None:
        # Route through start_scheduler so boot-time recovery (recover_missed_jobs)
        # runs in THIS process — the dedicated scheduler service is the canonical
        # job-trigger owner (docker-compose `scheduler`). The mgmt API lifespan
        # starts its own scheduler for control only and deliberately skips recovery
        # to avoid double-firing catch-up.
        await start_scheduler(sched)
        logger.info("scheduler: running — press Ctrl+C to stop")
        stop_event = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        await stop_event.wait()
        sched.shutdown(wait=True)
        logger.info("scheduler: shutdown complete")

    logging.basicConfig(level=settings.log_level.upper())
    asyncio.run(_run())
