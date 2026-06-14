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

    result["roe"] = (eps / nav).clip(-5, 5)
    result["payout_ratio"] = (cash_div / 100.0 / eps.abs().replace(0, np.nan)).clip(0, 3)

    # earnings quality: comprehensive income vs net profit (1.0 == clean)
    if "total_comprehensive_income_bdt" in result.columns and "net_profit_bdt" in result.columns:
        npft = pd.to_numeric(result["net_profit_bdt"], errors="coerce").replace(0, np.nan)
        tci = pd.to_numeric(result["total_comprehensive_income_bdt"], errors="coerce")
        result["earnings_quality"] = (tci / npft).clip(-2, 3)
    else:
        result["earnings_quality"] = np.nan

    # Clip extreme values (data errors in DSE filings)
    for col in ["eps_growth_1yr", "eps_growth_3yr", "nav_growth"]:
        result[col] = result[col].clip(-5, 5)

    return result


def compute_track_record_features(
    yearly: pd.DataFrame,
    rights_count_10y: int,
    inst_flow_pp: float | None,
    foreign_flow_pp: float | None,
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


def compute_leverage_features(
    short_loan_mn: float | None,
    long_loan_mn: float | None,
    market_cap_bdt: float | None,
    net_profit_bdt: float | None,
) -> dict[str, float]:
    """Leverage ratios from latest loan snapshot. Missing loan data -> NaN (not 0).

    Loan columns are in millions of BDT; market cap / profit are in BDT.
    """
    if short_loan_mn is None and long_loan_mn is None:
        return {"leverage_mktcap": np.nan, "leverage_profit": np.nan}
    total_loan_bdt = ((short_loan_mn or 0.0) + (long_loan_mn or 0.0)) * 1e6
    mktcap = float(market_cap_bdt) if market_cap_bdt is not None else np.nan
    profit = float(net_profit_bdt) if net_profit_bdt is not None else np.nan
    lev_mc = total_loan_bdt / mktcap if mktcap and mktcap > 0 else np.nan
    lev_pf = total_loan_bdt / profit if profit and profit > 0 else np.nan
    return {
        "leverage_mktcap": float(np.clip(lev_mc, 0, 50)) if not np.isnan(lev_mc) else np.nan,
        "leverage_profit": float(np.clip(lev_pf, 0, 100)) if not np.isnan(lev_pf) else np.nan,
    }


PILLAR_SPEC: dict[str, list[tuple[str, int]]] = {
    "growth":    [("eps_growth_1yr", 1), ("eps_growth_3yr", 1), ("profit_cagr_3y", 1),
                  ("profit_cagr_5y", 1), ("nav_growth", 1), ("quarterly_eps_yoy", 1)],
    "quality":   [("eps_consistency", 1), ("roe", 1), ("earnings_quality", 1)],
    "value":     [("pe_vs_sector", -1), ("pb_ratio", -1), ("div_yield", 1),
                  ("dividend_yield_pct", 1)],
    "dividends": [("dividend_streak", 1), ("cash_div_ratio_5y", 1), ("payout_ratio", 1)],
    "safety":    [("leverage_mktcap", -1), ("leverage_profit", -1), ("rights_count_10y", -1)],
    "ownership": [("inst_flow_pp", 1), ("foreign_flow_pp", 1), ("institution_pct", 1),
                  ("foreign_pct", 1)],
}


def compute_pillar_scores(cross: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional 0-100 pillar scores for a cohort of tickers.

    Each constituent feature is percentile-ranked across the cohort (pct=True),
    inverted where lower-is-better, then averaged per pillar and scaled to 0-100.
    NaN constituents are ignored in the per-pillar mean; a pillar with no usable
    constituents for a row scores NaN.
    """
    scores = pd.DataFrame(index=cross.index)
    for pillar, feats in PILLAR_SPEC.items():
        cols = []
        for feat, direction in feats:
            if feat not in cross.columns:
                continue
            pct = cross[feat].rank(pct=True)
            cols.append(pct if direction == 1 else (1.0 - pct))
        if cols:
            scores[pillar] = pd.concat(cols, axis=1).mean(axis=1, skipna=True) * 100.0
        else:
            scores[pillar] = np.nan
    return scores


def compute_quarterly_eps_yoy(quarterly: pd.DataFrame) -> float:
    """Latest quarter's basic EPS vs the same quarter one year earlier.

    quarterly columns: fiscal_year, quarter, eps_basic. Returns NaN if the
    prior-year same quarter is missing or non-positive.
    """
    if quarterly.empty:
        return np.nan
    q = quarterly.sort_values(["fiscal_year", "quarter"]).reset_index(drop=True)
    last = q.iloc[-1]
    fy, qtr = int(last["fiscal_year"]), int(last["quarter"])
    cur = pd.to_numeric(last["eps_basic"], errors="coerce")
    prior = q[(q["fiscal_year"] == fy - 1) & (q["quarter"] == qtr)]
    if prior.empty:
        return np.nan
    prev = pd.to_numeric(prior["eps_basic"], errors="coerce").iloc[0]
    if not np.isfinite(cur) or not np.isfinite(prev) or prev <= 0:
        return np.nan
    return float(cur / prev - 1)
