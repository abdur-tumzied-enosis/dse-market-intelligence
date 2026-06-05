"""Tests for ml.constants."""
from __future__ import annotations


def test_horizons_are_fibonacci():
    from ml.constants import HORIZONS
    assert HORIZONS == [1, 2, 3, 5, 8, 13]


def test_feature_cols_count():
    from ml.constants import PRICE_FEATURE_COLS
    # 10 legacy + 3 new open-based
    assert len(PRICE_FEATURE_COLS) == 13
    assert PRICE_FEATURE_COLS[:10] == [
        "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
        "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
    ]
    assert PRICE_FEATURE_COLS[10:] == ["gap_open", "body", "mid_return"]


def test_scalar_constants():
    from ml.constants import SEQ_LEN, EMA_SPAN, TOP_N, MIN_TICKERS_PER_DATE
    assert SEQ_LEN == 60
    assert EMA_SPAN == 3
    assert TOP_N == 20
    assert MIN_TICKERS_PER_DATE == 20
