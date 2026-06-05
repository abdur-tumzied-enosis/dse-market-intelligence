"""Shared ML constants — single source of truth for train + inference."""
from __future__ import annotations

SEQ_LEN = 60
HORIZONS = [1, 2, 3, 5, 8, 13]
EMA_SPAN = 3
TOP_N = 20
MIN_TICKERS_PER_DATE = 20

PRICE_FEATURE_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
    "gap_open", "body", "mid_return",
]
