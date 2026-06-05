"""Tests for ml.eval.backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _meta_with_preds():
    # 2 dates, 4 tickers each. raw_1 = realized 1d fwd return.
    rows = [
        # date d1
        {"time": "d1", "ticker": "A", "raw_1": 0.05, "pred": 3.0},
        {"time": "d1", "ticker": "B", "raw_1": 0.02, "pred": 2.0},
        {"time": "d1", "ticker": "C", "raw_1": -0.01, "pred": 1.0},
        {"time": "d1", "ticker": "D", "raw_1": -0.03, "pred": 0.0},
        # date d2
        {"time": "d2", "ticker": "A", "raw_1": -0.02, "pred": 0.0},
        {"time": "d2", "ticker": "B", "raw_1": 0.04, "pred": 3.0},
        {"time": "d2", "ticker": "C", "raw_1": 0.01, "pred": 2.0},
        {"time": "d2", "ticker": "D", "raw_1": -0.05, "pred": 1.0},
    ]
    return pd.DataFrame(rows)


def test_backtest_top_n_beats_market_when_pred_is_good():
    from ml.eval.backtest import run_backtest
    df = _meta_with_preds()
    res = run_backtest(df, pred_col="pred", ret_col="raw_1", top_n=2)
    # top-2 picks the higher-return names each date -> beats equal-weight mean
    assert res["strategy_cum_return"] > res["market_cum_return"]
    assert "sharpe" in res
    assert "max_drawdown" in res
    assert len(res["equity_curve"]) == 2


def test_backtest_handles_single_date():
    from ml.eval.backtest import run_backtest
    df = _meta_with_preds()
    df = df[df["time"] == "d1"]
    res = run_backtest(df, pred_col="pred", ret_col="raw_1", top_n=2)
    assert len(res["equity_curve"]) == 1
