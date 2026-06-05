"""Compute technical indicator features from OHLCV DataFrame."""
from __future__ import annotations

import numpy as np
import pandas as pd
import ta


def compute_price_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute technical indicators from OHLCV DataFrame.

    Args:
        df: DataFrame with columns [open, close, high, low, volume], DatetimeIndex.
            ``open`` is required; it is used to compute gap_open, body, and mid_return.
            Minimum 26 rows recommended for indicator warmup.

    Returns:
        DataFrame with original columns plus feature columns.
        NaN values appear in early rows during indicator warmup — caller
        should forward-fill or drop as needed.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    open_ = df["open"].astype(float)

    result = df.copy()

    result["rsi_14"] = ta.momentum.RSIIndicator(close, window=14).rsi()

    macd = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    result["macd_diff"] = macd.macd_diff()

    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    result["bb_pband"] = bb.bollinger_pband()

    atr = ta.volatility.AverageTrueRange(high, low, close, window=14)
    result["atr_norm"] = atr.average_true_range() / close.replace(0, np.nan)

    result["adx_14"] = ta.trend.ADXIndicator(high, low, close, window=14).adx()

    result["return_1d"] = close.pct_change(1)
    result["return_5d"] = close.pct_change(5)
    result["return_20d"] = close.pct_change(20)

    vol_mean = volume.rolling(20).mean()
    vol_std = volume.rolling(20).std().replace(0, np.nan)
    result["volume_zscore"] = (volume - vol_mean) / vol_std

    obv = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    result["obv"] = obv.pct_change(1)

    prev_close = close.shift(1)
    result["gap_open"] = open_ / prev_close - 1.0
    result["body"] = (close - open_) / open_.replace(0, np.nan)
    mid = (high + low) / 2.0
    result["mid_return"] = mid.pct_change(1)

    return result
