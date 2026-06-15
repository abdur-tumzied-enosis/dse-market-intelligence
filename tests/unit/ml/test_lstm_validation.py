from datetime import date

import numpy as np
import pandas as pd

from ml.eval.lstm_validation import (
    evaluate_results,
    select_oos,
    split_regime,
)


def _results_df(n_dates=20, n_tickers=30, seed=0):
    """Long results frame: one row per (time, ticker), with a single horizon's
    pred score and raw forward return. pred is correlated with fwd so signal exists."""
    rng = np.random.default_rng(seed)
    dates = pd.to_datetime([f"2021-{1 + d // 28:02d}-{1 + d % 28:02d}" for d in range(n_dates)])
    rows = []
    for t in dates:
        fwd = rng.normal(0, 0.05, n_tickers)
        pred = fwd + rng.normal(0, 0.02, n_tickers)  # correlated -> positive IC
        for i in range(n_tickers):
            rows.append({"time": t, "ticker": f"T{i}", "pred_5": pred[i], "fwd_ret_5": fwd[i]})
    return pd.DataFrame(rows)


def test_select_oos_keeps_only_purged_val_tail():
    df = _results_df()
    oos = select_oos(df, frac=0.8, gap_bars=2)
    # everything kept is at/after the purged boundary, and it's a strict subset
    assert len(oos) < len(df)
    assert oos["time"].min() > df["time"].min()


def test_split_regime_partitions_on_floor_window():
    df = _results_df()
    # inject a few rows inside the floor window
    floor_rows = df.head(3).copy()
    floor_rows["time"] = pd.Timestamp("2022-09-01")
    df2 = pd.concat([df, floor_rows], ignore_index=True)
    ex_floor = split_regime(df2, floor_start=date(2022, 7, 28), floor_end=date(2024, 1, 22))
    assert (ex_floor["time"] < pd.Timestamp("2022-07-28")).all() or (
        ex_floor["time"] > pd.Timestamp("2024-01-22")
    ).all()
    assert len(ex_floor) == len(df)  # the 3 floor rows removed


def test_evaluate_results_reports_positive_ic_for_correlated_signal():
    df = _results_df(seed=1)
    rep = evaluate_results(df, horizons=[5])
    blk = rep["horizons"][5]
    assert blk["rank_ic"]["mean_ic"] > 0.3        # strong correlation
    assert blk["hit_rate"] > 0.5
    assert "base_rate" in blk and "brier" in blk
