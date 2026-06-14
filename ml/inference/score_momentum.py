"""Compute cross-sectional momentum scores for all active tickers → stock_scores.

Run: python -m ml.inference.score_momentum

Uses the batch pool (direct db:5432) — the universe-wide close fetch is a heavy
query that stalls on the pgBouncer transaction pool.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime

import pandas as pd

from db.pool import get_batch_pool
from ml.scoring.momentum_score import compute_momentum_scores

MODEL_VERSION = "v1"
# Calendar-day lookback; ≥126 trading bars survive after weekend/holiday gaps.
_LOOKBACK_DAYS = 400
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def load_closes(pool) -> dict[str, pd.Series]:
    """Ascending daily close series per active ticker (latest close per calendar day)."""
    rows = await pool.fetch(
        """
        SELECT sp.ticker,
               sp.time::date AS day,
               (array_agg(sp.close ORDER BY sp.time DESC))[1] AS close
        FROM stock_prices sp
        JOIN companies c ON c.ticker = sp.ticker AND c.is_active = true
        WHERE sp.time >= NOW() - ($1 || ' days')::interval
          AND sp.close IS NOT NULL AND sp.close > 0
        GROUP BY sp.ticker, sp.time::date
        ORDER BY sp.ticker, day
        """,
        str(_LOOKBACK_DAYS),
    )
    by_ticker: dict[str, list[tuple[object, float]]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append((r["day"], float(r["close"])))
    return {
        ticker: pd.Series([c for _, c in pts], index=[d for d, _ in pts])
        for ticker, pts in by_ticker.items()
    }


async def write_momentum(pool, scores: dict[str, float], scored_at: datetime) -> int:
    """Upsert momentum_score into the current day's stock_scores row. Returns rows written."""
    scored_date = scored_at.date()
    count = 0
    for ticker, mom in scores.items():
        await pool.execute(
            """
            INSERT INTO stock_scores
                (ticker, scored_at, scored_date, momentum_score, model_version)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ticker, model_version, scored_date) DO UPDATE
                SET momentum_score = EXCLUDED.momentum_score,
                    scored_at      = EXCLUDED.scored_at
            """,
            ticker, scored_at, scored_date, mom, MODEL_VERSION,
        )
        count += 1
    return count


async def main() -> dict[str, int]:
    pool = await get_batch_pool()
    scored_at = datetime.now(UTC)

    closes = await load_closes(pool)
    log.info("loaded closes for %d tickers", len(closes))

    scores = compute_momentum_scores(closes)
    log.info("scored momentum for %d tickers", len(scores))

    written = await write_momentum(pool, scores, scored_at)
    log.info("wrote %d momentum rows", written)
    return {"scored": len(scores), "written": written}


if __name__ == "__main__":
    asyncio.run(main())
