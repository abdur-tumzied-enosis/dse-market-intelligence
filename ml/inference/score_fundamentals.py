"""
Run FundamentalScorer on all active tickers and write scores to stock_scores table.

Run: python -m ml.inference.score_fundamentals
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from db.pool import get_pool
from ml.features.feature_store import build_fundamental_feature_vector
from ml.models.fundamental_scorer import FEATURE_COLS, FundamentalScorer

MODEL_PATH = Path("models/v1/fundamental_scorer.pkl")
MODEL_VERSION = "v1"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def score_all_tickers(pool, scorer) -> dict[str, dict]:
    """Score all active tickers; return {ticker: {score, pillars, drivers}}.

    Builds every ticker's feature vector first so pillar scores (cross-sectional
    percentiles) and SHAP attributions are computed over the full scored cohort.
    """
    from ml.explain.fundamental_explainer import build_explanation
    from ml.features.fundamental_features import compute_pillar_scores

    tickers = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker")

    vectors: dict[str, pd.Series] = {}
    for row in tickers:
        ticker = row["ticker"]
        try:
            feat_vec = await build_fundamental_feature_vector(pool, ticker)
            if feat_vec.empty or feat_vec.isna().all():
                log.debug(f"skip {ticker}: no fundamental data")
                continue
            vectors[ticker] = feat_vec
        except Exception as exc:  # noqa: BLE001
            log.warning(f"skip {ticker}: {exc}")

    if not vectors:
        return {}

    cross = pd.DataFrame(vectors).T  # index = ticker, columns = feature names
    pillars_df = compute_pillar_scores(cross)
    feat_df = cross.reindex(columns=FEATURE_COLS)
    proba = scorer.predict_proba(feat_df)
    contribs = scorer.shap_contributions(feat_df)

    results: dict[str, dict] = {}
    for i, ticker in enumerate(cross.index):
        pillars = {k: (float(v) if pd.notna(v) else None)
                   for k, v in pillars_df.loc[ticker].items()}
        payload = build_explanation(
            headline=float(proba[i]) * 100.0,
            pillars={k: v for k, v in pillars.items() if v is not None},
            feature_row=cross.loc[ticker],
            contributions=contribs.iloc[i])
        results[ticker] = {
            "score": float(proba[i]),
            "pillars": pillars,
            "drivers": payload["drivers"],
        }
    return results


async def write_scores(pool, scores: dict[str, dict], scored_at: datetime) -> int:
    """Upsert fundamental_score + detail payload into stock_scores. Returns rows written."""
    scored_date = scored_at.date()
    count = 0
    for ticker, payload in scores.items():
        detail = {
            "score": payload["score"],
            "pillars": payload["pillars"],
            "drivers": payload["drivers"],
        }
        await pool.execute(
            """
            INSERT INTO stock_scores
                (ticker, scored_at, scored_date, fundamental_score,
                 fundamental_detail, model_version)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (ticker, model_version, scored_date) DO UPDATE
                SET fundamental_score   = EXCLUDED.fundamental_score,
                    fundamental_detail  = EXCLUDED.fundamental_detail,
                    scored_at           = EXCLUDED.scored_at
            """,
            ticker, scored_at, scored_date, payload["score"],
            json.dumps(detail), MODEL_VERSION,
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
    scored_at = datetime.now(UTC)
    scores = await score_all_tickers(pool, scorer)
    log.info(f"Scored {len(scores)} tickers")

    inserted = await write_scores(pool, scores, scored_at)
    log.info(f"Wrote {inserted} rows to stock_scores")


if __name__ == "__main__":
    asyncio.run(main())
