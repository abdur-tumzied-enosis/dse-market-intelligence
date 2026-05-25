"""
Train XGBoost fundamental scorer on historical fundamentals + price data.

Run: python -m ml.train.train_fundamental
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

from db.pool import get_pool
from ml.features.fundamental_features import compute_fundamental_features
from ml.models.fundamental_scorer import FEATURE_COLS, FundamentalScorer

MODEL_PATH = Path("models/v1/fundamental_scorer.pkl")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def build_training_dataset(pool) -> tuple[pd.DataFrame, pd.Series]:
    """
    Build feature matrix + binary labels from DB.

    Label = 1 if stock's close price 6 months after fiscal year end is higher
    than at fiscal year end (absolute return positive, cross-sectionally neutral).
    """
    rows = await pool.fetch(
        """
        SELECT f.ticker, f.fiscal_year,
               f.eps, f.nav, f.pe, f.cash_div_pct, f.stock_div_pct,
               (
                   SELECT sp2.pe FROM sector_pe sp2
                   JOIN companies c2 ON c2.sector = sp2.sector
                   WHERE c2.ticker = f.ticker
                     AND sp2.fetched_at <= make_date(f.fiscal_year, 12, 31)
                   ORDER BY sp2.fetched_at DESC LIMIT 1
               ) AS sector_pe,
               (
                   SELECT sp3.close FROM stock_prices sp3
                   WHERE sp3.ticker = f.ticker
                     AND sp3.time >= make_date(f.fiscal_year, 10, 1)
                     AND sp3.time <= make_date(f.fiscal_year + 1, 1, 31)
                   ORDER BY sp3.time DESC LIMIT 1
               ) AS price_at_fy_end,
               (
                   SELECT sp4.close FROM stock_prices sp4
                   WHERE sp4.ticker = f.ticker
                     AND sp4.time >= make_date(f.fiscal_year + 1, 4, 1)
                     AND sp4.time <= make_date(f.fiscal_year + 1, 9, 30)
                   ORDER BY sp4.time ASC LIMIT 1
               ) AS price_6m_later
        FROM fundamentals f
        WHERE f.fiscal_year IS NOT NULL AND f.eps IS NOT NULL
        ORDER BY f.ticker, f.fiscal_year
        """
    )

    df = pd.DataFrame(list(rows), columns=[
        "ticker", "fiscal_year", "eps", "nav", "pe",
        "cash_div_pct", "stock_div_pct", "sector_pe",
        "price_at_fy_end", "price_6m_later",
    ])
    for col in ["eps", "nav", "pe", "cash_div_pct", "stock_div_pct",
                "sector_pe", "price_at_fy_end", "price_6m_later"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    feature_rows = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("fiscal_year").reset_index(drop=True)
        feats = compute_fundamental_features(grp)
        feats["pe_vs_sector"] = grp["pe"] / grp["sector_pe"].replace(0, np.nan)
        feats["ticker"] = ticker
        feats["fiscal_year"] = grp["fiscal_year"]
        feats["price_at_fy_end"] = grp["price_at_fy_end"].values
        feats["price_6m_later"] = grp["price_6m_later"].values
        feature_rows.append(feats)

    full_df = pd.concat(feature_rows, ignore_index=True)
    full_df = full_df.dropna(subset=["price_at_fy_end", "price_6m_later"])
    full_df["label"] = (full_df["price_6m_later"] > full_df["price_at_fy_end"]).astype(int)
    full_df = full_df.dropna(subset=FEATURE_COLS + ["label"])

    X = full_df[FEATURE_COLS].reset_index(drop=True)
    y = full_df["label"].reset_index(drop=True)
    return X, y


async def main() -> None:
    pool = await get_pool()

    log.info("Building training dataset...")
    X, y = await build_training_dataset(pool)
    log.info(f"Training set: {len(X)} rows, {y.mean():.1%} positive labels")

    if len(X) < 30:
        log.warning("Very few training samples — model may not generalize")

    # Walk-forward cross-validation
    tscv = TimeSeriesSplit(n_splits=min(3, len(X) // 20))
    auc_scores = []
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        scorer = FundamentalScorer()
        scorer.fit(X.iloc[train_idx], y.iloc[train_idx])
        if len(y.iloc[val_idx].unique()) < 2:
            log.warning(f"Fold {fold}: only one class in validation — skipping AUC")
            continue
        proba = scorer.predict_proba(X.iloc[val_idx])
        auc = roc_auc_score(y.iloc[val_idx], proba)
        auc_scores.append(auc)
        log.info(f"  Fold {fold} AUC: {auc:.3f}")

    if auc_scores:
        log.info(f"Mean CV AUC: {sum(auc_scores) / len(auc_scores):.3f}")

    log.info("Training final model on full dataset...")
    final = FundamentalScorer()
    final.fit(X, y)

    log.info("Feature importances:")
    for feat, imp in sorted(final.feature_importances().items(), key=lambda x: -x[1]):
        log.info(f"  {feat}: {imp:.3f}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    final.save(MODEL_PATH)
    log.info(f"Model saved → {MODEL_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
