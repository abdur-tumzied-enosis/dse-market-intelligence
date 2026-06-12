"""Compute fundamental features from multi-year per-ticker fundamentals DataFrame."""
from __future__ import annotations

from typing import Optional

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

    result["roe"] = (eps / nav).clip(-5, 5)
    result["payout_ratio"] = (cash_div / 100.0 / eps.abs().replace(0, np.nan)).clip(0, 3)

    # Clip extreme values (data errors in DSE filings)
    for col in ["eps_growth_1yr", "eps_growth_3yr", "nav_growth"]:
        result[col] = result[col].clip(-5, 5)

    return result


def compute_track_record_features(
    yearly: pd.DataFrame,
    rights_count_10y: int,
    inst_flow_pp: Optional[float],
    foreign_flow_pp: Optional[float],
) -> dict[str, float]:
    """Track-record scalars from per-year fundamentals + pre-aggregated inputs.

    Rules (design §3.6): NULLs dropped per feature; windows use closed fiscal
    years only (caller passes closed years); CAGR is NaN on profit sign change.
    """
    df = yearly.sort_values("fiscal_year").reset_index(drop=True)
    profit = pd.to_numeric(df["net_profit_bdt"], errors="coerce")

    def _cagr(n_years: int) -> float:
        s = profit.dropna()
        if len(s) < n_years + 1:
            return np.nan
        first, last = float(s.iloc[-(n_years + 1)]), float(s.iloc[-1])
        if first <= 0 or last <= 0:  # sign change / nonpositive base → meaningless
            return np.nan
        return (last / first) ** (1.0 / n_years) - 1

    cash = pd.to_numeric(df["cash_div_pct"], errors="coerce").fillna(0)
    stock = pd.to_numeric(df["stock_div_pct"], errors="coerce").fillna(0)

    streak = 0
    for v in reversed(cash.tolist()):
        if v > 0:
            streak += 1
        else:
            break

    cash5, stock5 = float(cash.tail(5).sum()), float(stock.tail(5).sum())
    denom = cash5 + stock5

    return {
        "profit_cagr_3y": _cagr(3),
        "profit_cagr_5y": _cagr(5),
        "dividend_streak": float(streak),
        "cash_div_ratio_5y": cash5 / denom if denom > 0 else np.nan,
        "rights_count_10y": float(rights_count_10y),
        "inst_flow_pp": float(inst_flow_pp) if inst_flow_pp is not None else np.nan,
        "foreign_flow_pp": float(foreign_flow_pp) if foreign_flow_pp is not None else np.nan,
    }
