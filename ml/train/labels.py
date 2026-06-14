"""Label construction and walk-forward fold generation for the fundamental model."""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_neutralized_label(
    df: pd.DataFrame,
    min_sector_names: int = 5,
) -> pd.DataFrame:
    """Add raw_return, neutralized_return, and binary top-half label.

    Requires columns: fiscal_year, sector, price_at_fy_end, price_fwd.
    neutralized_return subtracts the (fiscal_year, sector) median return; cohorts
    with < min_sector_names fall back to the (fiscal_year) median. label = 1 if the
    row's neutralized_return is above the within-year median (top half).
    """
    out = df.copy()
    out["raw_return"] = (out["price_fwd"] - out["price_at_fy_end"]) / out["price_at_fy_end"]

    grp = out.groupby(["fiscal_year", "sector"])["raw_return"]
    sector_size = grp.transform("size")
    sector_med = grp.transform("median")
    year_med = out.groupby("fiscal_year")["raw_return"].transform("median")
    baseline = sector_med.where(sector_size >= min_sector_names, year_med)

    out["neutralized_return"] = out["raw_return"] - baseline
    yr_med_neut = out.groupby("fiscal_year")["neutralized_return"].transform("median")
    out["label"] = (out["neutralized_return"] > yr_med_neut).astype(int)
    return out


def walk_forward_folds(
    fiscal_years: pd.Series,
    embargo_years: int = 1,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding walk-forward folds keyed on fiscal_year with an embargo gap.

    For each cutoff year Y (ascending), train = rows with fiscal_year <= Y, test =
    rows with fiscal_year == Y + 1 + embargo_years. The embargo year(s) are excluded
    from both train and test so the forward-return label of the train set is realized
    before the test set's feature date (no lookahead).
    """
    fy = fiscal_years.reset_index(drop=True)
    years = sorted(fy.unique())
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for cutoff in years:
        test_year = cutoff + 1 + embargo_years
        if test_year not in years:
            continue
        train_idx = fy.index[fy <= cutoff].to_numpy()
        test_idx = fy.index[fy == test_year].to_numpy()
        if len(train_idx) and len(test_idx):
            folds.append((train_idx, test_idx))
    return folds
