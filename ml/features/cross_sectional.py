"""Cross-sectional helpers: forward returns, per-date z-scoring, windowing."""
from __future__ import annotations

import numpy as np
import pandas as pd


def clean_and_smooth(feats: pd.DataFrame, cols: list[str], ema_span: int) -> pd.DataFrame:
    """Per-ticker feature cleaning + causal EMA smoothing.

    Shared by training (build_sequences) and inference (build_price_feature_matrix)
    so the served feature distribution matches training exactly. Steps, in order:
    scrub +/-inf -> forward-fill -> fillna(0) -> causal EMA (adjust=False).
    Operates on a single ticker's time-ordered rows; the caller groups by ticker.
    Returns a DataFrame of just `cols`.
    """
    out = (
        feats[cols]
        .replace([np.inf, -np.inf], np.nan)
        .ffill()
        .fillna(0.0)
    )
    return out.ewm(span=ema_span, adjust=False).mean()


def forward_returns(close: pd.Series, horizons: list[int]) -> pd.DataFrame:
    """Forward return per horizon: fwd_ret_h[t] = close[t+h]/close[t] - 1.

    Tail rows where close[t+h] is unavailable are NaN.
    Caller must ensure close > 0; a zero close yields inf.
    """
    out = pd.DataFrame(index=close.index)
    close = close.astype(float)
    for h in horizons:
        out[f"fwd_ret_{h}"] = close.shift(-h) / close - 1.0
    return out


def cross_sectional_zscore(
    df: pd.DataFrame, cols: list[str], by: str = "time"
) -> pd.DataFrame:
    """Z-score `cols` within each `by` group (per-date cross-section).

    Zero-variance groups (or single-member) become 0.0, never NaN/inf.
    """
    out = df.copy()
    grp = df.groupby(by)[cols]
    mean = grp.transform("mean")
    # ddof=0 (population std): we normalize over the full observed cross-section,
    # not estimating a population from a sample.
    std = grp.transform(lambda x: x.std(ddof=0)).replace(0.0, np.nan)
    z = (df[cols] - mean) / std
    # NaN covers two cases: single-member group (std=NaN) and zero-variance group
    # (std=0.0 -> replaced with NaN above). Both are clamped to 0.0 here.
    out[cols] = z.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out


def build_windows(
    panel: pd.DataFrame,
    feature_cols: list[str],
    label_cols: list[str],
    raw_cols: list[str],
    seq_len: int,
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Slice per-ticker sequences from a long [time, ticker, ...] panel.

    Args:
        panel: long DataFrame, already cross-sectionally normalized, with a
            `time` and `ticker` column plus feature/label/raw columns.
        feature_cols: model inputs. Caller must guarantee `feature_cols` contain
            no NaN — only label NaN is filtered here; NaN features would silently
            corrupt training.
        label_cols: training targets (cross-sectional z of forward returns).
        raw_cols: raw forward returns, carried into meta for IC/backtest.
        seq_len: window length.

    Returns:
        X: (N, seq_len, n_features) float32
        y: (N, n_labels) float32
        meta: DataFrame (N rows) with [time, ticker, *raw_cols] aligned to X/y.
        Samples whose label row contains NaN (no future return) are dropped.
    """
    Xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    meta_rows: list[dict] = []

    for ticker, grp in panel.groupby("ticker", sort=False):
        grp = grp.sort_values("time").reset_index(drop=True)
        feat = grp[feature_cols].to_numpy(dtype=np.float32)
        lab = grp[label_cols].to_numpy(dtype=np.float32)
        raw = grp[raw_cols].to_numpy(dtype=np.float32)
        times = grp["time"].to_numpy()
        n = len(grp)
        for t in range(seq_len - 1, n):
            label = lab[t]
            if np.isnan(label).any():
                continue
            Xs.append(feat[t - seq_len + 1: t + 1])
            ys.append(label)
            row = {"time": times[t], "ticker": ticker}
            for j, rc in enumerate(raw_cols):
                row[rc] = float(raw[t, j])
            meta_rows.append(row)

    if not Xs:
        raise ValueError("No windows built — check panel size vs seq_len")

    return np.stack(Xs), np.stack(ys), pd.DataFrame(meta_rows)
