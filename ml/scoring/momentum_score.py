"""Cross-sectional momentum score (0–1), centered on the global market average.

0.5 = a stock moving with the market. >0.5 beats the market, <0.5 lags it.

Built from excess-vs-universe price returns (21 / 63 / 126 trading-day windows ≈
1m / 3m / 6m) plus RSI-14 and price-vs-MA50. Each signal is z-scored across the
scored universe (so the global average sits at z=0), blended, then squashed with a
logistic so the output centers on 0.5. The z-score IS the "global average center":
subtracting the cross-sectional mean makes every signal market-relative.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import ta

# Trading-day lookbacks ≈ 1m / 3m / 6m.
_RET_WINDOWS = {"ret_21": 21, "ret_63": 63, "ret_126": 126}
# Need enough history for the 63d return plus RSI/MA warmup; 126d is best-effort.
_MIN_BARS = 70
_WEIGHTS = {
    "ret_21":       0.30,
    "ret_63":       0.35,
    "ret_126":      0.20,
    "rsi_centered": 0.10,
    "px_vs_ma50":   0.05,
}
_Z_CLIP = 3.0  # winsorize z-scores to tame illiquid-ticker spikes


def _raw_signals(close: pd.Series) -> dict[str, float] | None:
    """Per-ticker raw momentum signals from an ascending close series.

    Returns None when there is too little clean history to score the ticker.
    """
    close = close.astype(float)
    close = close[close > 0].dropna()
    if len(close) < _MIN_BARS:
        return None
    last = float(close.iloc[-1])
    sig: dict[str, float] = {}
    for name, w in _RET_WINDOWS.items():
        sig[name] = last / float(close.iloc[-1 - w]) - 1.0 if len(close) > w else np.nan
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    sig["rsi_centered"] = (float(rsi) - 50.0) if pd.notna(rsi) else np.nan
    ma50 = close.rolling(50).mean().iloc[-1]
    sig["px_vs_ma50"] = (last / float(ma50) - 1.0) if pd.notna(ma50) and ma50 > 0 else np.nan
    return sig


def _zscore(col: pd.Series) -> pd.Series:
    """Global-average-centered z-score, winsorized. Degenerate spread → all zeros."""
    std = col.std(skipna=True)
    if not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=col.index)
    return ((col - col.mean(skipna=True)) / std).clip(-_Z_CLIP, _Z_CLIP)


def compute_momentum_scores(closes: dict[str, pd.Series]) -> dict[str, float]:
    """Map {ticker: ascending daily close series} → {ticker: momentum_score in [0,1]}.

    Centered on the global market average (0.5). Tickers with too little history
    are omitted from the result (caller leaves their score null).
    """
    raw = {
        t: s
        for t, s in ((t, _raw_signals(c)) for t, c in closes.items())
        if s is not None
    }
    if len(raw) < 2:  # z-scores need a cohort to center against
        return {}
    feat = pd.DataFrame(raw).T  # index = ticker, columns = signal
    # Missing signals (e.g. no 126d window yet) z-score to 0 = neutral in the blend.
    z = pd.DataFrame({col: _zscore(feat[col]) for col in _WEIGHTS}).fillna(0.0)
    weights = pd.Series(_WEIGHTS)
    blended = z[list(_WEIGHTS)].mul(weights, axis=1).sum(axis=1)  # Series, index=ticker
    # logistic → (0,1), 0.5 at the market average
    score = blended.apply(lambda r: 1.0 / (1.0 + np.exp(-r)))
    return {str(t): round(float(v), 4) for t, v in score.items()}
