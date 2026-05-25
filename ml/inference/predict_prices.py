"""
Run LSTM inference on all active tickers and write to ml_predictions.

Run: python -m ml.inference.predict_prices
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from db.pool import get_pool
from ml.features.feature_store import build_price_feature_matrix
from ml.models.lstm_predictor import LSTMPredictor

MODEL_PATH = Path("models/v1/lstm_v0.pt")
MODEL_VERSION = "lstm_v0"
SEQ_LEN = 60
HORIZONS = [5, 10, 20]
PRICE_FEATURE_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
]
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def predict_ticker(pool, model: LSTMPredictor, ticker: str) -> None:
    """Run LSTM for one ticker and write 3 rows (one per horizon) to ml_predictions."""
    feat_df = await build_price_feature_matrix(pool, ticker, lookback_days=SEQ_LEN + 5)

    if feat_df.empty or len(feat_df) < SEQ_LEN:
        log.debug(f"skip {ticker}: insufficient price data ({len(feat_df)} rows)")
        return

    available_cols = [c for c in PRICE_FEATURE_COLS if c in feat_df.columns]
    window = feat_df[available_cols].tail(SEQ_LEN).values.astype(np.float32)

    # Pad features to match model input_size if columns differ
    if window.shape[1] < len(PRICE_FEATURE_COLS):
        pad = np.zeros((SEQ_LEN, len(PRICE_FEATURE_COLS) - window.shape[1]), dtype=np.float32)
        window = np.hstack([window, pad])

    x = torch.from_numpy(window).unsqueeze(0)  # (1, 60, n_features)
    proba = model.predict_proba(x).squeeze(0)  # (3,)

    predicted_at = datetime.now(timezone.utc)
    last_close = float(feat_df["close"].iloc[-1]) if "close" in feat_df.columns else None

    for i, horizon in enumerate(HORIZONS):
        p = float(proba[i])
        predicted_direction = "up" if p >= 0.5 else "down"
        confidence = p if predicted_direction == "up" else 1.0 - p
        target_price = (last_close * (1 + (p - 0.5) * 0.1)) if last_close else None

        await pool.execute(
            """
            INSERT INTO ml_predictions
                (ticker, predicted_at, horizon_days, predicted_direction, confidence,
                 target_price, model_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            ticker, predicted_at, horizon, predicted_direction, confidence,
            target_price, MODEL_VERSION,
        )


async def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"LSTM model not found at {MODEL_PATH}. Run ml.train.train_lstm first."
        )

    pool = await get_pool()
    model = LSTMPredictor.load(MODEL_PATH)
    model.eval()

    tickers = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
    )
    log.info(f"Running LSTM inference on {len(tickers)} tickers...")

    ok, skipped = 0, 0
    for row in tickers:
        ticker = row["ticker"]
        try:
            await predict_ticker(pool, model, ticker)
            ok += 1
        except Exception as exc:
            log.warning(f"skip {ticker}: {exc}")
            skipped += 1

    log.info(f"Done. ok={ok} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
