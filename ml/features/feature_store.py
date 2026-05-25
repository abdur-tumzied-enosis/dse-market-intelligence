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
        SELECT time, close, high, low, volume
        FROM stock_prices
        WHERE ticker = $1 AND time >= $2 AND time <= $3
          AND close IS NOT NULL AND quality_flag != 'bad'
        ORDER BY time
        """,
        ticker, start, end,
    )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(list(rows), columns=["time", "close", "high", "low", "volume"])
    df = df.set_index("time").sort_index()
    df = df.astype(float)

    features = compute_price_features(df)
    features = features.ffill().fillna(0)
    return features.tail(lookback_days)


async def build_fundamental_feature_vector(pool, ticker: str) -> pd.Series:
    """
    Fetch multi-year fundamentals from DB and return latest feature vector.

    Returns Series with keys: eps_growth_1yr, eps_growth_3yr, nav_growth,
    div_yield, eps_consistency, pe_vs_sector. Missing values are NaN.
    Returns empty Series if no fundamental data found.
    """
    from ml.features.fundamental_features import compute_fundamental_features

    rows = await pool.fetch(
        """
        SELECT f.fiscal_year, f.eps, f.nav, f.pe, f.cash_div_pct, f.stock_div_pct,
               (
                   SELECT sp.pe FROM sector_pe sp
                   JOIN companies c ON c.sector = sp.sector
                   WHERE c.ticker = f.ticker
                   ORDER BY sp.fetched_at DESC LIMIT 1
               ) AS sector_pe
        FROM fundamentals f
        WHERE f.ticker = $1 AND f.fiscal_year IS NOT NULL
        ORDER BY f.fiscal_year
        """,
        ticker,
    )

    if not rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(
        list(rows),
        columns=["fiscal_year", "eps", "nav", "pe", "cash_div_pct", "stock_div_pct", "sector_pe"],
    )
    for col in ["eps", "nav", "pe", "cash_div_pct", "stock_div_pct", "sector_pe"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    features = compute_fundamental_features(df)
    latest = features.iloc[-1]

    sector_pe = float(df["sector_pe"].iloc[-1]) if pd.notna(df["sector_pe"].iloc[-1]) else np.nan
    pe = float(df["pe"].iloc[-1]) if pd.notna(df["pe"].iloc[-1]) else np.nan
    pe_vs_sector = pe / sector_pe if (sector_pe and sector_pe != 0) else np.nan

    return pd.Series({
        "eps_growth_1yr": float(latest.get("eps_growth_1yr", np.nan)),
        "eps_growth_3yr": float(latest.get("eps_growth_3yr", np.nan)),
        "nav_growth":     float(latest.get("nav_growth", np.nan)),
        "div_yield":      float(latest.get("div_yield", np.nan)),
        "eps_consistency":float(latest.get("eps_consistency", np.nan)),
        "pe_vs_sector":   pe_vs_sector,
    })
