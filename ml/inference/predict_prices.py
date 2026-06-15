"""
Run LSTM inference across all active tickers (cross-sectional) and write
ranked predictions to ml_predictions.

Run: python -m ml.inference.predict_prices
"""
from __future__ import annotations

import asyncio
import logging
import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import torch

from db.pool import get_pool
from ml.constants import HORIZONS, MIN_TICKERS_PER_DATE, PRICE_FEATURE_COLS, SEQ_LEN
from ml.features.cross_sectional import cross_sectional_zscore
from ml.features.feature_store import build_price_feature_matrix
from ml.models.lstm_predictor import LSTMPredictor

if TYPE_CHECKING:
    import numpy.typing as npt
    from sklearn.isotonic import IsotonicRegression

MODEL_PATH = Path("models/v1/lstm_v0.pt")
MODEL_VERSION = "lstm_v2_cal"
CALIBRATORS_PATH = Path("models/v1/lstm_direction_calibrators.pkl")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def _build_latest_panel(pool, tickers: list[str]) -> pd.DataFrame:
    """For each ticker, fetch the last SEQ_LEN+buffer days of features, tag with
    a per-day index so we can cross-sectionally z-score across tickers."""
    frames = []
    for tk in tickers:
        feat_df = await build_price_feature_matrix(pool, tk, lookback_days=SEQ_LEN + 30)
        avail = [c for c in PRICE_FEATURE_COLS if c in feat_df.columns]
        if feat_df.empty or len(feat_df) < SEQ_LEN or len(avail) < len(PRICE_FEATURE_COLS):
            continue
        g = feat_df.tail(SEQ_LEN).copy()
        g = g.reset_index().rename(columns={g.index.name or "index": "time"})
        if "time" not in g.columns:
            g = g.rename(columns={g.columns[0]: "time"})
        g["ticker"] = tk
        g["step"] = range(len(g))  # 0..SEQ_LEN-1, aligns dates across tickers
        frames.append(g[["step", "ticker", *PRICE_FEATURE_COLS]])
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def calibrated_direction(
    scores: npt.ArrayLike,
    calibrator: IsotonicRegression | None,
    low_signal: bool,
) -> tuple[list[str], np.ndarray]:
    """Map raw per-ticker scores for one horizon to (directions, confidences).

    With a usable calibrator: confidence = calibrated P of the STATED side
    (P(up) for an 'up' call, 1-P(up) for 'down'). Without one, or when the
    horizon is low-signal, confidence is a neutral 0.5 — honest 'no conviction' —
    while direction still follows the sign of the score.
    """
    scores = np.asarray(scores, dtype=float)
    directions = ["up" if s >= 0 else "down" for s in scores]
    if calibrator is None or low_signal:
        return directions, np.full(len(scores), 0.5)
    p_up = np.clip(calibrator.predict(scores), 0.0, 1.0)
    directions = ["up" if p >= 0.5 else "down" for p in p_up]
    confidences = np.where(p_up >= 0.5, p_up, 1.0 - p_up)
    return directions, confidences


def _load_calibrators() -> tuple[dict[int, IsotonicRegression], dict[int, dict[str, object]]]:
    """Returns (calibrators dict, metadata dict). Empty if the file is absent —
    inference then writes neutral confidence everywhere (honest degraded mode)."""
    if not CALIBRATORS_PATH.exists():
        log.warning("No calibrators at %s — writing neutral 0.5 confidence.", CALIBRATORS_PATH)
        return {}, {}
    with CALIBRATORS_PATH.open("rb") as f:
        blob = pickle.load(f)
    return blob.get("calibrators", {}), blob.get("metadata", {})


async def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"LSTM model not found at {MODEL_PATH}. Run ml.train.train_lstm first."
        )

    pool = await get_pool()
    model = LSTMPredictor.load(MODEL_PATH)
    model.eval()

    rows = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
    )
    tickers = [r["ticker"] for r in rows]
    log.info(f"Building cross-sectional panel for {len(tickers)} tickers...")

    panel = await _build_latest_panel(pool, tickers)
    if panel.empty or panel["ticker"].nunique() < MIN_TICKERS_PER_DATE:
        log.warning("Insufficient tickers for cross-sectional inference; aborting.")
        return

    # Cross-sectional z-score per aligned step, then window per ticker.
    panel = cross_sectional_zscore(panel, PRICE_FEATURE_COLS, by="step")

    windows, kept = [], []
    for tk, g in panel.groupby("ticker", sort=False):
        g = g.sort_values("step")
        if len(g) < SEQ_LEN:
            continue
        windows.append(g[PRICE_FEATURE_COLS].tail(SEQ_LEN).to_numpy(np.float32))
        kept.append(tk)

    x = torch.from_numpy(np.stack(windows))           # (n_tickers, SEQ_LEN, n_feat)
    preds = model.predict(x).numpy()                  # (n_tickers, n_horizons)

    predicted_at = datetime.now(UTC)
    prediction_date = predicted_at.date()

    calibrators, cal_meta = _load_calibrators()

    for hi, horizon in enumerate(HORIZONS):
        scores = preds[:, hi]
        iso = calibrators.get(horizon)
        low_signal = bool(cal_meta.get(horizon, {}).get("low_signal", False))
        directions, confidences = calibrated_direction(scores, iso, low_signal)
        for ti, ticker in enumerate(kept):
            await pool.execute(
                """
                INSERT INTO ml_predictions
                    (ticker, predicted_at, prediction_date, horizon_days,
                     predicted_direction, confidence, target_price, model_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (ticker, horizon_days, model_version, prediction_date) DO UPDATE
                    SET predicted_direction = EXCLUDED.predicted_direction,
                        confidence          = EXCLUDED.confidence,
                        target_price        = EXCLUDED.target_price,
                        predicted_at        = EXCLUDED.predicted_at
                """,
                ticker, predicted_at, prediction_date, horizon,
                directions[ti], float(confidences[ti]), None, MODEL_VERSION,
            )

    log.info(f"Done. wrote {len(kept)} tickers × {len(HORIZONS)} horizons.")


if __name__ == "__main__":
    asyncio.run(main())
