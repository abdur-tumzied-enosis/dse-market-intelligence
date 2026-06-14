"""
One-time backfill of DSEX/DS30/DSES daily history into index_daily.

Pulls the ~30-day history bdshare's get_market_info() already returns and seeds
index_daily so the /market/regime moving-average is usable on day one.

Usage:
    python -m extraction.bulk_load.index_history_loader
"""
from __future__ import annotations

import asyncio
import logging

import pandas as pd

from db.pool import get_pool
from extraction.normalizers import to_decimal

logger = logging.getLogger(__name__)

# bdshare columns after .strip().upper() (see extraction/adapters/bdshare/market_info.py)
_COL = {"dsex": "DSEX INDEX", "ds30": "DS30 INDEX", "dses": "DSES INDEX"}


async def backfill_index_history() -> dict:
    try:
        import bdshare as bd
    except ImportError:
        logger.error("index_backfill: bdshare not installed")
        return {"inserted": 0, "rows": 0}

    # bdshare hits dsebd.org, which serves an incomplete cert chain. Point
    # requests/urllib at the pinned-intermediate bundle or every fetch raises
    # CERTIFICATE_VERIFY_FAILED under Linux/Docker.
    from extraction.adapters.dse_direct._tls import use_dse_ca_for_requests

    use_dse_ca_for_requests()

    raw = bd.get_market_info()
    if raw is None or len(raw) == 0:
        logger.warning("index_backfill: get_market_info returned empty")
        return {"inserted": 0, "rows": 0}

    df = raw.copy()
    df.columns = [c.strip().upper() for c in df.columns]

    pool = await get_pool()
    inserted = 0
    for _, row in df.iterrows():
        # bdshare emits DATE as DD-MM-YYYY; dayfirst avoids month/day swap.
        dt = pd.to_datetime(row.get("DATE"), errors="coerce", dayfirst=True)
        if pd.isna(dt):
            logger.warning("index_backfill: unparseable DATE=%r — skipping", row.get("DATE"))
            continue
        d = dt.date()
        rec = await pool.fetchrow(
            """
            INSERT INTO index_daily (date, dsex, ds30, dses, source)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (date) DO NOTHING
            RETURNING date
            """,
            d,
            to_decimal(row.get(_COL["dsex"])),
            to_decimal(row.get(_COL["ds30"])),
            to_decimal(row.get(_COL["dses"])),
            "bdshare_backfill",
        )
        if rec:
            inserted += 1

    logger.info("index_backfill_done inserted=%d rows=%d", inserted, len(df))
    return {"inserted": inserted, "rows": len(df)}


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    asyncio.run(backfill_index_history())
