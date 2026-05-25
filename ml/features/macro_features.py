"""Compute derived macro features from macro_indicators pivot DataFrame."""
from __future__ import annotations

import pandas as pd


def compute_macro_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute derived macro features.

    Args:
        df: Wide DataFrame indexed by date. Columns may include any subset of
            [usd_bdt, policy_rate, cpi]. Missing columns are skipped.

    Returns:
        DataFrame with added derived columns for present columns only.
    """
    result = df.copy()

    if "usd_bdt" in df.columns:
        result["usd_bdt_change_20d"] = df["usd_bdt"].pct_change(20)

    if "policy_rate" in df.columns:
        result["policy_rate_delta"] = df["policy_rate"].diff(1)

    if "cpi" in df.columns:
        result["cpi_trend"] = df["cpi"].pct_change(12)

    return result
