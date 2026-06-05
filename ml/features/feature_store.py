"""Fetch data from DB and assemble feature matrices for ML models."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


async def build_price_feature_matrix(
    pool,
    ticker: str,
    lookback_days: int = 90,
) -> pd.DataFrame:
    """
    Fetch OHLCV from DB, compute price features, forward-fill NaN.

    Returns DataFrame with DatetimeIndex and price feature columns.
    Returns empty DataFrame if no price data found.
    """
    from ml.features.price_features import compute_price_features

    end = datetime.now(timezone.utc)
    # Fetch extra days for indicator warmup (ADX needs 28, MACD needs 26+9=35)
    start = end - timedelta(days=lookback_days + 40)

    rows = await pool.fetch(
        """
        SELECT time, open, close, high, low, volume
        FROM stock_prices
        WHERE ticker = $1 AND time >= $2 AND time <= $3
          AND close IS NOT NULL AND quality_flag != 'bad'
        ORDER BY time
        """,
        ticker, start, end,
    )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(list(rows), columns=["time", "open", "close", "high", "low", "volume"])
    df = df.set_index("time").sort_index()
    df = df.astype(float)

    from ml.constants import EMA_SPAN, PRICE_FEATURE_COLS
    from ml.features.cross_sectional import clean_and_smooth

    features = compute_price_features(df)
    # Identical cleaning + EMA smoothing as training (per ticker) to avoid
    # train/serve skew; build_price_feature_matrix is called once per ticker.
    features[PRICE_FEATURE_COLS] = clean_and_smooth(features, PRICE_FEATURE_COLS, EMA_SPAN)
    return features.tail(lookback_days)


async def build_fundamental_feature_vector(pool, ticker: str) -> pd.Series:
    """
    Fetch multi-year fundamentals from DB and return latest feature vector.

    Returns Series matching FEATURE_COLS in fundamental_scorer. Missing values are NaN.
    Returns empty Series if no fundamental data found.
    """
    from ml.features.fundamental_features import compute_fundamental_features

    rows = await pool.fetch(
        """
        SELECT f.fiscal_year, f.eps, f.nav, f.pe, f.cash_div_pct, f.stock_div_pct,
               (
                   SELECT sp.close FROM stock_prices sp
                   WHERE sp.ticker = f.ticker
                     AND sp.time >= make_date(f.fiscal_year, 10, 1)
                     AND sp.time <= make_date(f.fiscal_year + 1, 1, 31)
                   ORDER BY sp.time DESC LIMIT 1
               ) AS price_at_fy_end,
               -- sector-specific median PE for this fiscal year
               (
                   SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY f2.pe)
                   FROM fundamentals f2
                   JOIN companies c2 ON c2.ticker = f2.ticker
                   WHERE f2.fiscal_year = f.fiscal_year AND f2.pe > 0
                     AND c2.sector = c.sector
               ) AS median_pe
        FROM fundamentals f
        JOIN companies c ON c.ticker = f.ticker
        WHERE f.ticker = $1 AND f.fiscal_year IS NOT NULL
        ORDER BY f.fiscal_year
        """,
        ticker,
    )

    if not rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(
        list(rows),
        columns=["fiscal_year", "eps", "nav", "pe", "cash_div_pct", "stock_div_pct",
                 "price_at_fy_end", "median_pe"],
    )
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    features = compute_fundamental_features(df)
    latest = features.iloc[-1]

    pe = float(df["pe"].iloc[-1]) if pd.notna(df["pe"].iloc[-1]) else np.nan
    median_pe = float(df["median_pe"].iloc[-1]) if pd.notna(df["median_pe"].iloc[-1]) else np.nan
    pe_vs_sector = float(np.clip(pe / median_pe, 0, 10)) if (median_pe and median_pe != 0) else np.nan

    nav = float(df["nav"].iloc[-1]) if pd.notna(df["nav"].iloc[-1]) else np.nan
    price = float(df["price_at_fy_end"].iloc[-1]) if pd.notna(df["price_at_fy_end"].iloc[-1]) else np.nan
    pb_ratio = float(np.clip(price / nav, 0, 20)) if (nav and nav != 0 and not np.isnan(price)) else np.nan

    return pd.Series({
        "eps_growth_1yr": float(latest.get("eps_growth_1yr", np.nan)),
        "eps_growth_3yr": float(latest.get("eps_growth_3yr", np.nan)),
        "nav_growth":     float(latest.get("nav_growth", np.nan)),
        "div_yield":      float(latest.get("div_yield", np.nan)),
        "eps_consistency":float(latest.get("eps_consistency", np.nan)),
        "roe":            float(latest.get("roe", np.nan)),
        "payout_ratio":   float(latest.get("payout_ratio", np.nan)),
        "pe_vs_sector":   pe_vs_sector,
        "pb_ratio":       pb_ratio,
    })
