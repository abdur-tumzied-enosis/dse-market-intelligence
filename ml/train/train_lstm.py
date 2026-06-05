"""
Train cross-sectional rank-return LSTM (shared model across all tickers).

60-day input window → cross-sectionally z-scored forward-return labels for
horizons [1,2,3,5,8,13]. Selection: rank predicted rank-return, buy top-N.

Run: python -m ml.train.train_lstm
     python -m ml.train.train_lstm --dump dataset.npz
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, TensorDataset

from db.pool import get_pool
from ml.constants import (
    EMA_SPAN,
    HORIZONS,
    MIN_TICKERS_PER_DATE,
    PRICE_FEATURE_COLS,
    SEQ_LEN,
    TOP_N,
)
from ml.eval.metrics import rank_ic, top_n_hit_rate
from ml.features.cross_sectional import (
    build_windows,
    cross_sectional_zscore,
    forward_returns,
)
from ml.features.price_features import compute_price_features
from ml.models.lstm_predictor import LSTMPredictor

MODEL_PATH = Path("models/v1/lstm_v0.pt")
LABEL_COLS = [f"y_{h}" for h in HORIZONS]
RAW_COLS = [f"fwd_ret_{h}" for h in HORIZONS]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


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

        feats = compute_price_features(grp.set_index("time"))
        feats = (
            feats[PRICE_FEATURE_COLS]
            .replace([np.inf, -np.inf], np.nan)
            .ffill()
            .fillna(0.0)
        )
        # EMA smoothing (causal, per ticker) to cut daily noise
        feats = feats.ewm(span=EMA_SPAN, adjust=False).mean()

        fwd = forward_returns(grp.set_index("time")["close"], HORIZONS)

        panel = feats.copy()
        for h in HORIZONS:
            panel[f"fwd_ret_{h}"] = fwd[f"fwd_ret_{h}"].to_numpy()
        panel["ticker"] = ticker
        panel = panel.reset_index().rename(columns={"index": "time"})
        if "time" not in panel.columns:  # index name was already "time"
            panel = panel.rename(columns={panel.columns[0]: "time"})
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

    X, y, meta = build_windows(
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


async def main() -> None:
    pool = await get_pool()

    log.info("Building training sequences (may take 1–2 min)...")
    X, y = await build_sequences(pool)
    log.info(f"Sequences: {X.shape}, Labels: {y.shape}")
    log.info(f"Label distribution (5d/10d/20d up): {y.mean(axis=0)}")

    # Train/val split (80/20 chronological — do NOT shuffle to avoid leakage)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    # Fit scaler on train rows only; apply to both (no leakage)
    n_train, seq_len, n_feat = X_train.shape
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train.reshape(-1, n_feat)).reshape(n_train, seq_len, n_feat).astype(np.float32)
    n_val = X_val.shape[0]
    X_val = scaler.transform(X_val.reshape(-1, n_feat)).reshape(n_val, seq_len, n_feat).astype(np.float32)
    log.info(f"Feature means (train): {scaler.mean_.round(3)}")
    log.info(f"Feature stds  (train): {scaler.scale_.round(3)}")

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_ds   = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    train_dl = DataLoader(train_ds, batch_size=64, shuffle=True)
    val_dl   = DataLoader(val_ds, batch_size=256, shuffle=False)

    model = LSTMPredictor(input_size=len(PRICE_FEATURE_COLS), dropout=0.4)
    optimizer = torch.optim.Adam(model.parameters(), lr=3e-4, weight_decay=1e-4)
    criterion = nn.BCEWithLogitsLoss()

    best_val_loss = float("inf")
    patience = 5
    patience_count = 0

    for epoch in range(50):
        model.train()
        train_losses = []
        for xb, yb in train_dl:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(loss.item())

        model.eval()
        val_losses = []
        with torch.no_grad():
            for xb, yb in val_dl:
                val_losses.append(criterion(model(xb), yb).item())

        train_loss = sum(train_losses) / len(train_losses)
        val_loss   = sum(val_losses) / len(val_losses)
        log.info(f"Epoch {epoch+1:02d} | train={train_loss:.4f} val={val_loss:.4f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_count = 0
            model.save(MODEL_PATH, scaler=scaler)
            log.info(f"  → saved (val_loss={val_loss:.4f})")
        else:
            patience_count += 1
            if patience_count >= patience:
                log.info(f"Early stopping at epoch {epoch+1}")
                break

    log.info(f"Training complete. Best val_loss={best_val_loss:.4f}")
    log.info(f"Model saved → {MODEL_PATH}")


async def dump(out_path: Path) -> None:
    pool = await get_pool()
    log.info("Building sequences...")
    X, y = await build_sequences(pool)
    split = int(len(X) * 0.8)
    n_train, seq_len, n_feat = X[:split].shape
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X.reshape(-1, n_feat)).reshape(len(X), seq_len, n_feat).astype("float32")
    np.savez_compressed(
        out_path,
        X=X_scaled,
        y=y,
        feature_names=np.array(PRICE_FEATURE_COLS),
        scaler_mean=scaler.mean_,
        scaler_scale=scaler.scale_,
    )
    log.info(f"Saved → {out_path}  (X={X_scaled.shape}, y={y.shape})")
    log.info("Load with: data = np.load('dataset.npz'); X=data['X']; y=data['y']")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", metavar="PATH", help="dump dataset to .npz and exit")
    args = parser.parse_args()

    if args.dump:
        asyncio.run(dump(Path(args.dump)))
    else:
        asyncio.run(main())
