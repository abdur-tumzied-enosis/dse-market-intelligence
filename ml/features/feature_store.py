"""Fetch data from DB and assemble feature matrices for ML models."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

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

    end = datetime.now(UTC)
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
               f.net_profit_bdt, f.total_comprehensive_income_bdt,
               f.dividend_yield_pct, f.institution_pct, f.foreign_pct,
               f.eps_basis,
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

    df = pd.DataFrame([dict(r) for r in rows])
    numeric_cols = [c for c in df.columns if c != "eps_basis"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    features = compute_fundamental_features(df)
    latest = features.iloc[-1]
    last = df.iloc[-1]

    pe = float(last["pe"]) if pd.notna(last["pe"]) else np.nan
    median_pe = float(last["median_pe"]) if pd.notna(last["median_pe"]) else np.nan
    pe_vs_sector = float(np.clip(pe / median_pe, 0, 10)) if (median_pe and median_pe != 0) else np.nan
    nav = float(last["nav"]) if pd.notna(last["nav"]) else np.nan
    price = float(last["price_at_fy_end"]) if pd.notna(last["price_at_fy_end"]) else np.nan
    pb_ratio = float(np.clip(price / nav, 0, 20)) if (nav and nav != 0 and not np.isnan(price)) else np.nan

    latest_fy = int(last["fiscal_year"]) if pd.notna(last["fiscal_year"]) else None
    rights = await pool.fetchval(
        """SELECT count(*) FROM corporate_actions
           WHERE ticker = $1 AND action_type = 'right_issue'
             AND fiscal_year <= $2 AND fiscal_year >= $2 - 10""",
        ticker, latest_fy) if latest_fy is not None else 0
    flows = await pool.fetchrow(
        """SELECT (last.institution_pct - first.institution_pct) AS inst_flow,
                  (last.foreign_pct - first.foreign_pct) AS foreign_flow
           FROM (SELECT * FROM shareholding_history WHERE ticker = $1
                 ORDER BY as_on_date ASC LIMIT 1) AS first,
                (SELECT * FROM shareholding_history WHERE ticker = $1
                 ORDER BY as_on_date DESC LIMIT 1) AS last""", ticker)
    qrows = await pool.fetch(
        """SELECT fiscal_year, quarter, eps_basic FROM fundamentals_quarterly
           WHERE ticker = $1 ORDER BY fiscal_year, quarter""", ticker)
    qdf = pd.DataFrame([dict(r) for r in qrows]) if qrows else pd.DataFrame(
        columns=["fiscal_year", "quarter", "eps_basic"])

    from ml.features.fundamental_features import (
        compute_quarterly_eps_yoy,
        compute_track_record_features,
    )
    track = compute_track_record_features(
        df, rights_count_10y=rights or 0,
        inst_flow_pp=float(flows["inst_flow"]) if flows and flows["inst_flow"] is not None else None,
        foreign_flow_pp=float(flows["foreign_flow"]) if flows and flows["foreign_flow"] is not None else None,
    )

    result = pd.Series({
        "eps_growth_1yr":     float(latest.get("eps_growth_1yr", np.nan)),
        "eps_growth_3yr":     float(latest.get("eps_growth_3yr", np.nan)),
        "profit_cagr_3y":     track["profit_cagr_3y"],
        "profit_cagr_5y":     track["profit_cagr_5y"],
        "nav_growth":         float(latest.get("nav_growth", np.nan)),
        "quarterly_eps_yoy":  compute_quarterly_eps_yoy(qdf),
        "eps_consistency":    float(latest.get("eps_consistency", np.nan)),
        "roe":                float(latest.get("roe", np.nan)),
        "earnings_quality":   float(latest.get("earnings_quality", np.nan)),
        "pe_vs_sector":       pe_vs_sector,
        "pb_ratio":           pb_ratio,
        "div_yield":          float(latest.get("div_yield", np.nan)),
        "dividend_yield_pct": float(last["dividend_yield_pct"]) if pd.notna(last.get("dividend_yield_pct")) else np.nan,
        "dividend_streak":    track["dividend_streak"],
        "cash_div_ratio_5y":  track["cash_div_ratio_5y"],
        "payout_ratio":       float(latest.get("payout_ratio", np.nan)),
        "rights_count_10y":   track["rights_count_10y"],
        "inst_flow_pp":       track["inst_flow_pp"],
        "foreign_flow_pp":    track["foreign_flow_pp"],
        "institution_pct":    float(last["institution_pct"]) if pd.notna(last.get("institution_pct")) else np.nan,
        "foreign_pct":        float(last["foreign_pct"]) if pd.notna(last.get("foreign_pct")) else np.nan,
    })
    return result
