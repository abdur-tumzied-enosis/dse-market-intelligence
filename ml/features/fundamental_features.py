"""Compute fundamental features from multi-year per-ticker fundamentals DataFrame."""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_fundamental_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute derived features from annual fundamentals.

    Args:
        df: DataFrame with columns [fiscal_year, eps, nav, pe, cash_div_pct,
            stock_div_pct]. One row per fiscal year, any order.

    Returns:
        DataFrame sorted by fiscal_year with added columns:
        eps_growth_1yr, eps_growth_3yr, nav_growth, div_yield, eps_consistency.
        pe_vs_sector is NOT computed here — it requires a sector_pe join.
    """
    result = df.sort_values("fiscal_year").copy().reset_index(drop=True)

    eps = result["eps"].astype(float).replace(0, np.nan)
    nav = result["nav"].astype(float).replace(0, np.nan)
    pe = result["pe"].astype(float).replace(0, np.nan)
    cash_div = result["cash_div_pct"].astype(float).fillna(0)

    result["eps_growth_1yr"] = eps.pct_change(1)
    result["eps_growth_3yr"] = (eps / eps.shift(3)).pow(1.0 / 3) - 1
    result["nav_growth"] = nav.pct_change(1)
    result["div_yield"] = (cash_div / 100.0) / pe

    eps_std = eps.rolling(3, min_periods=2).std()
    eps_mean = eps.rolling(3, min_periods=2).mean().abs().replace(0, np.nan)
    result["eps_consistency"] = 1.0 / (1.0 + (eps_std / eps_mean).fillna(1.0))

    # Clip extreme values (data errors in DSE filings)
    for col in ["eps_growth_1yr", "eps_growth_3yr", "nav_growth"]:
        result[col] = result[col].clip(-5, 5)

    return result
