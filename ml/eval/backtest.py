"""Top-N basket backtest over per-date predictions vs realized returns."""
from __future__ import annotations

import numpy as np
import pandas as pd


def run_backtest(
    df: pd.DataFrame,
    pred_col: str,
    ret_col: str,
    top_n: int,
) -> dict:
    """Long-only top-N basket, equal weight, one rebalance per date.

    Args:
        df: rows with columns [time, <pred_col>, <ret_col>]; `ret_col` is the
            realized forward return for the held horizon.
        pred_col: prediction column to rank by (descending).
        ret_col: realized return column for PnL.
        top_n: basket size per date.

    Returns:
        dict with per-date strategy/market returns, cumulative returns,
        Sharpe (per-period, unannualized), max drawdown, and the equity curve.
    """
    strat_rets: list[float] = []
    mkt_rets: list[float] = []
    dates: list = []

    for date, g in df.groupby("time", sort=True):
        g = g.dropna(subset=[pred_col, ret_col])
        if g.empty:
            continue
        k = min(top_n, len(g))
        top = g.nlargest(k, pred_col)
        strat_rets.append(float(top[ret_col].mean()))
        mkt_rets.append(float(g[ret_col].mean()))
        dates.append(date)

    strat = np.asarray(strat_rets, dtype=float)
    mkt = np.asarray(mkt_rets, dtype=float)

    equity = np.cumprod(1.0 + strat)
    market_equity = np.cumprod(1.0 + mkt)
    excess = strat - mkt
    sharpe = (
        float(excess.mean() / excess.std())
        if len(excess) > 1 and excess.std() > 0
        else float("nan")
    )
    peak = np.maximum.accumulate(equity) if len(equity) else np.array([1.0])
    drawdown = (equity / peak - 1.0) if len(equity) else np.array([0.0])
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    return {
        "dates": dates,
        "strategy_returns": strat.tolist(),
        "market_returns": mkt.tolist(),
        "equity_curve": equity.tolist(),
        "market_equity_curve": market_equity.tolist(),
        "strategy_cum_return": float(equity[-1] - 1.0) if len(equity) else 0.0,
        "market_cum_return": float(market_equity[-1] - 1.0) if len(market_equity) else 0.0,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
    }
