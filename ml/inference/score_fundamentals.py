"""
Run FundamentalScorer on all active tickers and write scores to stock_scores table.

Run: python -m ml.inference.score_fundamentals
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from db.pool import get_pool
from ml.features.feature_store import build_fundamental_feature_vector
from ml.models.fundamental_scorer import FEATURE_COLS, FundamentalScorer

MODEL_PATH = Path("models/v1/fundamental_scorer.pkl")
MODEL_VERSION = "v1"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def score_all_tickers(pool, scorer: FundamentalScorer) -> dict[str, float]:
    """
    Score all active tickers with FundamentalScorer.

    Returns dict mapping ticker → fundamental_score (0.0–1.0).
    Tickers with missing fundamental data are skipped.
    """
    tickers = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
    )

    scores: dict[str, float] = {}
    for row in tickers:
        ticker = row["ticker"]
        try:
            feat_vec = await build_fundamental_feature_vector(pool, ticker)
            if feat_vec.empty or feat_vec.isna().all():
                log.debug(f"skip {ticker}: no fundamental data")
                continue
            feat_df = pd.DataFrame([feat_vec])
            proba = scorer.predict_proba(feat_df)
            scores[ticker] = float(proba[0])
        except Exception as exc:
            log.warning(f"skip {ticker}: {exc}")

    return scores


async def write_scores(pool, scores: dict[str, float], scored_at: datetime) -> int:
    """Upsert fundamental_score into stock_scores. Returns rows inserted."""
    scored_date = scored_at.date()
    count = 0
    for ticker, score in scores.items():
        await pool.execute(
            """
            INSERT INTO stock_scores
                (ticker, scored_at, scored_date, fundamental_score, model_version)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ticker, model_version, scored_date) DO UPDATE
                SET fundamental_score = EXCLUDED.fundamental_score,
                    scored_at         = EXCLUDED.scored_at
            """,
            ticker, scored_at, scored_date, score, MODEL_VERSION,
        )
        count += 1
    return count


async def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH}. Run ml.train.train_fundamental first."
        )

    pool = await get_pool()
    scorer = FundamentalScorer()
    scorer.load(MODEL_PATH)

    log.info("Scoring all active tickers...")
    scored_at = datetime.now(timezone.utc)
    scores = await score_all_tickers(pool, scorer)
    log.info(f"Scored {len(scores)} tickers")

    inserted = await write_scores(pool, scores, scored_at)
    log.info(f"Wrote {inserted} rows to stock_scores")


if __name__ == "__main__":
    asyncio.run(main())
