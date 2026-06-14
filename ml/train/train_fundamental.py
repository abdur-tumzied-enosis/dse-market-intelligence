"""
Train the calibrated fundamental scorer on enriched fundamentals + price data.

Run inside the docker worker (host cannot reach the DB):
    docker exec dse_worker python -m ml.train.train_fundamental
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from db.pool import get_pool
from ml.features.fundamental_features import (
    compute_fundamental_features,
    compute_quarterly_eps_yoy,
    compute_track_record_features,
)
from ml.models.fundamental_scorer import FEATURE_COLS, FundamentalScorer
from ml.train.labels import add_neutralized_label, walk_forward_folds

MODEL_PATH = Path("models/v1/fundamental_scorer.pkl")
HORIZON_MONTHS = 12  # primary label horizon; set 6 for the secondary label
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def rank_ic(proba: np.ndarray, target: np.ndarray) -> float:
    """Spearman rank correlation between predicted proba and realized return."""
    if len(np.unique(target)) < 2 or len(np.unique(proba)) < 2:
        return float("nan")
    rho, _ = spearmanr(proba, target)
    return float(rho)


def asof_track_row(
    grp: pd.DataFrame,
    as_of_year: int,
    rights_count_10y: int,
    inst_flow_pp: float | None,
    foreign_flow_pp: float | None,
) -> dict[str, float]:
    """Track-record features computed using only rows with fiscal_year <= as_of_year."""
    past = grp[grp["fiscal_year"] <= as_of_year]
    return compute_track_record_features(
        past, rights_count_10y=rights_count_10y,
        inst_flow_pp=inst_flow_pp, foreign_flow_pp=foreign_flow_pp,
    )


_SQL = """
SELECT f.ticker, f.fiscal_year,
       f.eps, f.nav, f.pe, f.cash_div_pct, f.stock_div_pct,
       f.net_profit_bdt, f.total_comprehensive_income_bdt, f.dividend_yield_pct,
       f.institution_pct, f.foreign_pct,
       c.sector,
       (SELECT sp.close FROM stock_prices sp
          WHERE sp.ticker = f.ticker
            AND sp.time >= make_date(f.fiscal_year, 10, 1)
            AND sp.time <= make_date(f.fiscal_year + 1, 1, 31)
          ORDER BY sp.time DESC LIMIT 1) AS price_at_fy_end,
       (SELECT sp.close FROM stock_prices sp
          WHERE sp.ticker = f.ticker
            AND sp.time >= make_date(f.fiscal_year + 1, 4, 1)
            AND sp.time <= make_date(f.fiscal_year + 1, 9, 30)
          ORDER BY sp.time ASC LIMIT 1) AS price_6m_later,
       (SELECT sp.close FROM stock_prices sp
          WHERE sp.ticker = f.ticker
            AND sp.time >= make_date(f.fiscal_year + 1, 10, 1)
            AND sp.time <= make_date(f.fiscal_year + 2, 3, 31)
          ORDER BY sp.time ASC LIMIT 1) AS price_12m_later
FROM fundamentals f
JOIN companies c ON c.ticker = f.ticker
WHERE f.fiscal_year IS NOT NULL AND f.eps IS NOT NULL
ORDER BY f.ticker, f.fiscal_year
"""

_NUMERIC = ["eps", "nav", "pe", "cash_div_pct", "stock_div_pct", "net_profit_bdt",
            "total_comprehensive_income_bdt", "dividend_yield_pct", "institution_pct",
            "foreign_pct", "price_at_fy_end", "price_6m_later", "price_12m_later"]


async def _asof_scalars(pool, ticker: str, as_of_year: int) -> dict:
    """Per-(ticker, year) scalars needing their own queries: rights, flows, quarterly."""
    rights = await pool.fetchval(
        """SELECT count(*) FROM corporate_actions
           WHERE ticker = $1 AND action_type = 'right_issue' AND fiscal_year <= $2
             AND fiscal_year >= $2 - 10""", ticker, as_of_year)
    flows = await pool.fetchrow(
        """WITH s AS (SELECT * FROM shareholding_history
                      WHERE ticker = $1 AND as_on_date <= make_date($2 + 1, 6, 30))
           SELECT (SELECT institution_pct FROM s ORDER BY as_on_date DESC LIMIT 1)
                - (SELECT institution_pct FROM s ORDER BY as_on_date ASC LIMIT 1) AS inst_flow,
                  (SELECT foreign_pct FROM s ORDER BY as_on_date DESC LIMIT 1)
                - (SELECT foreign_pct FROM s ORDER BY as_on_date ASC LIMIT 1) AS foreign_flow
        """, ticker, as_of_year)
    qrows = await pool.fetch(
        """SELECT fiscal_year, quarter, eps_basic FROM fundamentals_quarterly
           WHERE ticker = $1 AND fiscal_year <= $2 ORDER BY fiscal_year, quarter""",
        ticker, as_of_year)
    qdf = pd.DataFrame([dict(r) for r in qrows]) if qrows else pd.DataFrame(
        columns=["fiscal_year", "quarter", "eps_basic"])
    return {
        "rights": rights or 0,
        "inst_flow": float(flows["inst_flow"]) if flows and flows["inst_flow"] is not None else None,
        "foreign_flow": float(flows["foreign_flow"]) if flows and flows["foreign_flow"] is not None else None,
        "quarterly_eps_yoy": compute_quarterly_eps_yoy(qdf),
    }


async def build_training_dataset(pool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (X over FEATURE_COLS, meta with fiscal_year/ticker/label/neutralized_return)."""
    rows = await pool.fetch(_SQL)
    df = pd.DataFrame([dict(r) for r in rows])
    for col in _NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    fwd_col = "price_12m_later" if HORIZON_MONTHS == 12 else "price_6m_later"

    feature_rows = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("fiscal_year").reset_index(drop=True)
        feats = compute_fundamental_features(grp)
        feats["ticker"] = ticker
        feats["fiscal_year"] = grp["fiscal_year"].values
        feats["sector"] = grp["sector"].values
        feats["pe_raw"] = grp["pe"].values
        feats["nav_raw"] = grp["nav"].astype(float).replace(0, np.nan).values
        feats["dividend_yield_pct"] = grp["dividend_yield_pct"].values
        feats["institution_pct"] = grp["institution_pct"].values
        feats["foreign_pct"] = grp["foreign_pct"].values
        feats["price_at_fy_end"] = grp["price_at_fy_end"].values
        feats["price_fwd"] = grp[fwd_col].values

        for i, row in feats.iterrows():
            yr = int(row["fiscal_year"])
            sc = await _asof_scalars(pool, ticker, yr)
            track = asof_track_row(grp, yr, sc["rights"], sc["inst_flow"], sc["foreign_flow"])
            for k, v in track.items():
                feats.at[i, k] = v
            feats.at[i, "quarterly_eps_yoy"] = sc["quarterly_eps_yoy"]
        feature_rows.append(feats)

    full = pd.concat(feature_rows, ignore_index=True)
    full = full.dropna(subset=["price_at_fy_end", "price_fwd"])

    sector_median_pe = full.groupby(["fiscal_year", "sector"])["pe_raw"].transform("median")
    full["pe_vs_sector"] = (full["pe_raw"] / sector_median_pe.replace(0, np.nan)).clip(0, 10)
    full["pb_ratio"] = (full["price_at_fy_end"] / full["nav_raw"]).clip(0, 20)

    full = add_neutralized_label(full)  # adds raw_return, neutralized_return, label (float, NaN-capable)
    full = full.dropna(subset=["label"])  # drop zero-base-price rows whose label is NaN
    full = full.sort_values(["fiscal_year", "ticker"]).reset_index(drop=True)

    X = full[FEATURE_COLS].reset_index(drop=True)
    meta = full[["fiscal_year", "ticker", "label", "neutralized_return"]].reset_index(drop=True)
    meta["label"] = meta["label"].astype(int)
    return X, meta


async def main() -> None:
    pool = await get_pool()
    log.info("Building training dataset...")
    X, meta = await build_training_dataset(pool)
    y = meta["label"]
    log.info(f"Training set: {len(X)} rows, {y.mean():.1%} positive labels, "
             f"{meta['fiscal_year'].nunique()} fiscal years")

    if len(X) < 200:
        log.error("Fewer than 200 trainable rows — aborting (would not generalize).")
        await pool.close()
        return

    folds = walk_forward_folds(meta["fiscal_year"], embargo_years=1)
    ics, aucs = [], []
    for fold, (tr, te) in enumerate(folds):
        scorer = FundamentalScorer()
        scorer.fit(X.iloc[tr], y.iloc[tr])
        proba = scorer.predict_proba(X.iloc[te])
        ic = rank_ic(proba, meta["neutralized_return"].iloc[te].to_numpy())
        ics.append(ic)
        if y.iloc[te].nunique() == 2:
            aucs.append(roc_auc_score(y.iloc[te], proba))
        log.info(f"  Fold {fold}: train={len(tr)} test={len(te)} rank_IC={ic:.3f}")
    if ics:
        log.info(f"Mean rank IC: {np.nanmean(ics):.3f}  Mean AUC: "
                 f"{np.nanmean(aucs) if aucs else float('nan'):.3f}")

    log.info("Training final calibrated model on full dataset...")
    final = FundamentalScorer()
    final.fit(X, y)
    log.info("Feature importances:")
    for feat, imp in sorted(final.feature_importances().items(), key=lambda kv: -kv[1]):
        log.info(f"  {feat}: {imp:.3f}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    final.save(MODEL_PATH)
    log.info(f"Model saved → {MODEL_PATH}")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
