"""Tests for ml.train.labels."""
from __future__ import annotations

import pandas as pd


def _frame() -> pd.DataFrame:
    # two sectors, one fiscal year; price_fwd encodes known returns
    return pd.DataFrame({
        "ticker":          ["A", "B", "C", "D", "E", "F"],
        "fiscal_year":     [2022, 2022, 2022, 2022, 2022, 2022],
        "sector":          ["Bank", "Bank", "Bank", "Bank", "Bank", "Bank"],
        "price_at_fy_end": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        "price_fwd":       [130.0, 120.0, 110.0, 105.0, 100.0, 90.0],
    })


def test_label_top_half_within_year():
    from ml.train.labels import add_neutralized_label
    out = add_neutralized_label(_frame(), min_sector_names=5)
    # 6 names, balanced top half → exactly 3 positives
    assert out["label"].sum() == 3
    # best return is positive-labelled, worst is negative
    assert out.loc[out["ticker"] == "A", "label"].iloc[0] == 1
    assert out.loc[out["ticker"] == "F", "label"].iloc[0] == 0


def test_label_sector_neutralized_subtracts_cohort_median():
    from ml.train.labels import add_neutralized_label
    out = add_neutralized_label(_frame(), min_sector_names=5)
    # raw returns: 0.30,0.20,0.10,0.05,0.00,-0.10 ; median 0.075
    a = out.loc[out["ticker"] == "A", "neutralized_return"].iloc[0]
    assert abs(a - (0.30 - 0.075)) < 1e-9


def test_label_falls_back_to_year_median_for_sparse_sector():
    from ml.train.labels import add_neutralized_label
    df = _frame()
    df.loc[0, "sector"] = "Tiny"  # sector of size 1 < min_sector_names
    out = add_neutralized_label(df, min_sector_names=5)
    # Tiny-sector row uses YEAR median (not its own 1-row sector median)
    year_med = ((df["price_fwd"] - df["price_at_fy_end"]) / df["price_at_fy_end"]).median()
    a = out.loc[out["ticker"] == "A", "neutralized_return"].iloc[0]
    assert abs(a - (0.30 - year_med)) < 1e-9


def test_walk_forward_folds_embargo():
    from ml.train.labels import walk_forward_folds
    years = pd.Series([2020, 2021, 2022, 2023, 2024])
    folds = walk_forward_folds(years, embargo_years=1)
    # train<=2020 test 2022 ; train<=2021 test 2023 ; train<=2022 test 2024
    assert len(folds) == 3
    train_idx, test_idx = folds[0]
    assert set(years.iloc[train_idx]) == {2020}
    assert set(years.iloc[test_idx]) == {2022}
