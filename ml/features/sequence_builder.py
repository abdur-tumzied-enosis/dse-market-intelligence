"""Shared price-sequence pipeline for training and evaluation.

Both ml.train.train_lstm and ml.eval.lstm_validation build windows the SAME way
(per-ticker clean_and_smooth -> forward returns -> cross-sectional z-score ->
build_windows) so served/evaluated features match training exactly. Keep this the
single source of truth (train/serve parity invariant).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ml.constants import (
    EMA_SPAN,
    HORIZONS,
    MIN_TICKERS_PER_DATE,
    PRICE_FEATURE_COLS,
    SEQ_LEN,
)
from ml.features.cross_sectional import (
    build_windows,
    clean_and_smooth,
    cross_sectional_zscore,
    forward_returns,
)
from ml.features.price_features import compute_price_features

LABEL_COLS = [f"y_{h}" for h in HORIZONS]
RAW_COLS = [f"fwd_ret_{h}" for h in HORIZONS]


async def load_price_rows(pool) -> pd.DataFrame:
    """Fetch full OHLCV for all quality-ok tickers as a long DataFrame."""
    rows = await pool.fetch(
        """
        SELECT ticker, time, open, close, high, low, volume
        FROM stock_prices
        WHERE quality_flag = 'ok' AND close IS NOT NULL
          AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
        ORDER BY ticker, time
        """
    )
    df = pd.DataFrame(
        list(rows),
        columns=["ticker", "time", "open", "close", "high", "low", "volume"],
    )
    for col in ["open", "close", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def build_sequences(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Long OHLCV DataFrame → (X, y, meta).

    Pipeline: per-ticker features + EMA smoothing + forward returns → long panel
    → per-date cross-sectional z-score of features and forward returns → drop
    thin dates → 60-day windows.

    Returns:
        X: (N, SEQ_LEN, n_features) float32
        y: (N, n_horizons) float32 — cross-sectional z of forward returns
        meta: DataFrame (N rows): time, ticker, raw fwd_ret_h per horizon
    """
    panels: list[pd.DataFrame] = []
    max_h = max(HORIZONS)

    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("time").reset_index(drop=True)
        if len(grp) < SEQ_LEN + max_h:
            continue

        feats_full = compute_price_features(grp.set_index("time"))
        missing = set(PRICE_FEATURE_COLS) - set(feats_full.columns)
        if missing:
            raise KeyError(f"compute_price_features missing columns: {sorted(missing)}")
        # Clean + causal EMA smooth (shared with inference to avoid train/serve
        # skew). Early indicator-warmup rows become 0.0; harmless because windows
        # start at t>=SEQ_LEN-1 and the SEQ_LEN+max_horizon length filter excludes
        # most warmup contamination.
        feats = clean_and_smooth(feats_full, PRICE_FEATURE_COLS, EMA_SPAN)

        fwd = forward_returns(grp.set_index("time")["close"], HORIZONS)

        # feats and fwd share the same time index (both from this sorted grp);
        # assign by label so a future row-drop in compute_price_features can't
        # silently misalign labels with features.
        assert feats.index.equals(fwd.index), f"feature/label index mismatch for {ticker}"
        panel = feats.copy()
        for h in HORIZONS:
            panel[f"fwd_ret_{h}"] = fwd[f"fwd_ret_{h}"]
        panel["ticker"] = ticker
        panel = panel.reset_index()  # index named "time" -> column "time"
        assert "time" in panel.columns, f"expected 'time' column, got {list(panel.columns)}"
        panels.append(panel)

    if not panels:
        raise ValueError("No tickers with enough history — check stock_prices")

    long_df = pd.concat(panels, ignore_index=True)

    # Drop thin cross-sections (too few tickers to z-score meaningfully)
    counts = long_df.groupby("time")["ticker"].transform("count")
    long_df = long_df[counts >= MIN_TICKERS_PER_DATE].reset_index(drop=True)

    # Cross-sectional z-score of features (model inputs)
    long_df = cross_sectional_zscore(long_df, PRICE_FEATURE_COLS, by="time")

    # Cross-sectional z-score of forward returns -> training labels y_h.
    # Keep raw fwd_ret_h alongside for IC/backtest. Rows with NaN fwd_ret
    # (tail of each ticker) get y=NaN and are dropped by build_windows.
    z = cross_sectional_zscore(long_df, RAW_COLS, by="time")
    for h in HORIZONS:
        long_df[f"y_{h}"] = z[f"fwd_ret_{h}"]
        # restore NaN where the raw forward return was missing (no future)
        long_df.loc[long_df[f"fwd_ret_{h}"].isna(), f"y_{h}"] = np.nan

    X, y, meta = build_windows(  # noqa: N806
        long_df,
        feature_cols=PRICE_FEATURE_COLS,
        label_cols=LABEL_COLS,
        raw_cols=RAW_COLS,
        seq_len=SEQ_LEN,
    )
    nan_count = int(np.isnan(X).sum())
    if nan_count:
        raise ValueError(f"NaN in feature array after cleaning: {nan_count}")
    return X, y, meta


def trading_calendar(times: np.ndarray) -> pd.DatetimeIndex:
    """Sorted, de-duplicated union of all sample dates — the master trading
    calendar. DSE trades Sun-Thu with holidays, so calendar gaps are irregular;
    using actual observed dates (not a fixed frequency) is the only correct base
    for purging by trading bars."""
    return pd.DatetimeIndex(pd.to_datetime(np.unique(times))).sort_values()


def date_boundary(times: np.ndarray, frac: float) -> pd.Timestamp:
    """Train/val cut on a DATE boundary (not a sample-index boundary), so no
    single date straddles train and val. Returns the date at the `frac` quantile
    of unique sorted dates; callers assign dates < boundary to train, >= to val."""
    uniq = pd.DatetimeIndex(pd.to_datetime(np.unique(times))).sort_values()
    idx = min(int(len(uniq) * frac), len(uniq) - 1)
    return uniq[idx]


def advance_on_calendar(
    calendar: pd.DatetimeIndex, start: pd.Timestamp, n_bars: int
) -> pd.Timestamp:
    """Advance `n_bars` trading bars forward from `start` on the calendar.
    Clamps to the last calendar date if it runs off the end."""
    pos = int(calendar.searchsorted(start, side="left"))
    return calendar[min(pos + n_bars, len(calendar) - 1)]


def purged_val_start(
    calendar: pd.DatetimeIndex, boundary: pd.Timestamp, gap_bars: int
) -> pd.Timestamp:
    """First val date kept after purging. Train labels look forward up to
    gap_bars from the last train date, so val samples earlier than that overlap
    train labels and must be dropped. last_train_date = largest date < boundary;
    advance gap_bars trading bars from it."""
    train_dates = calendar[calendar < boundary]
    if len(train_dates) == 0:
        return boundary
    return advance_on_calendar(calendar, train_dates[-1], gap_bars)
