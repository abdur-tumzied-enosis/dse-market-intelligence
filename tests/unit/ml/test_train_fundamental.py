"""Tests for the pure helpers in ml.train.train_fundamental."""
from __future__ import annotations

import numpy as np
import pandas as pd


def test_rank_ic_perfect_ranking_is_one():
    from ml.train.train_fundamental import rank_ic
    proba = np.array([0.1, 0.4, 0.6, 0.9])
    target = np.array([-0.05, 0.0, 0.1, 0.3])
    assert rank_ic(proba, target) > 0.99


def test_rank_ic_handles_constant_target_as_nan():
    import math

    from ml.train.train_fundamental import rank_ic
    assert math.isnan(rank_ic(np.array([0.1, 0.2, 0.3]), np.array([0.0, 0.0, 0.0])))


def test_asof_track_features_use_only_past_rows():
    from ml.train.train_fundamental import asof_track_row
    grp = pd.DataFrame({
        "fiscal_year":   [2020, 2021, 2022, 2023],
        "net_profit_bdt": [100e6, 120e6, 140e6, 9_999e6],  # 2023 spike must NOT leak
        "cash_div_pct":  [10.0, 10.0, 10.0, 50.0],
        "stock_div_pct": [0.0, 0.0, 0.0, 0.0],
    })
    feats = asof_track_row(grp, as_of_year=2022, rights_count_10y=0,
                           inst_flow_pp=None, foreign_flow_pp=None)
    # dividend_streak as-of 2022 = 3 (2020,2021,2022), NOT 4
    assert feats["dividend_streak"] == 3
