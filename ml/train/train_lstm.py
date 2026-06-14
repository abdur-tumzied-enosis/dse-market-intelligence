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
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from db.pool import get_pool
from ml.constants import (
    HORIZONS,
    PRICE_FEATURE_COLS,
    TOP_N,
)
from ml.eval.metrics import rank_ic, top_n_hit_rate
from ml.features.sequence_builder import build_sequences, load_price_rows
from ml.models.lstm_predictor import LSTMPredictor

MODEL_PATH = Path("models/v1/lstm_v0.pt")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _epoch_metrics(model, X_val, meta_val) -> dict[str, float]:  # noqa: N803
    """Compute per-horizon mean rank IC and top-N hit rate over val dates."""
    model.eval()
    with torch.no_grad():
        preds = model(torch.from_numpy(X_val)).numpy()  # (N, n_horizons)
    out: dict[str, float] = {}
    df = meta_val.reset_index(drop=True)
    for hi, h in enumerate(HORIZONS):
        ics, hits = [], []
        actual_col = f"fwd_ret_{h}"
        df_h = df.assign(_pred=preds[:, hi])
        for _, g in df_h.groupby("time"):
            a = g[actual_col].to_numpy()
            p = g["_pred"].to_numpy()
            ic = rank_ic(p, a)
            if not np.isnan(ic):
                ics.append(ic)
            hits.append(top_n_hit_rate(p, a, n=TOP_N))
        out[f"ic_{h}"] = float(np.mean(ics)) if ics else float("nan")
        out[f"hit_{h}"] = float(np.mean(hits)) if hits else float("nan")
    return out


async def main() -> None:
    pool = await get_pool()
    log.info("Loading price rows...")
    df = await load_price_rows(pool)
    log.info("Building training sequences (may take 1–2 min)...")
    X, y, meta = build_sequences(df)  # noqa: N806
    log.info(f"Sequences: {X.shape}, Labels: {y.shape}")

    # Chronological split by sample time (no shuffle — avoids leakage)
    order = np.argsort(meta["time"].to_numpy(), kind="stable")
    X, y, meta = X[order], y[order], meta.iloc[order].reset_index(drop=True)  # noqa: N806
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]  # noqa: N806
    y_train, y_val = y[:split], y[split:]
    meta_val = meta.iloc[split:].reset_index(drop=True)

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    train_dl = DataLoader(train_ds, batch_size=64, shuffle=True)
    val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    val_dl = DataLoader(val_ds, batch_size=256, shuffle=False)

    model = LSTMPredictor(input_size=len(PRICE_FEATURE_COLS), dropout=0.4)
    optimizer = torch.optim.Adam(model.parameters(), lr=3e-4, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=3
    )
    criterion = nn.HuberLoss()

    best_val_loss = float("inf")
    patience, patience_count = 5, 0

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
        val_loss = sum(val_losses) / len(val_losses)
        scheduler.step(val_loss)

        m = _epoch_metrics(model, X_val, meta_val)
        ic_str = " ".join(f"IC{h}={m[f'ic_{h}']:+.3f}" for h in HORIZONS)
        log.info(
            f"Epoch {epoch+1:02d} | train={train_loss:.4f} val={val_loss:.4f} | {ic_str}"
        )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_count = 0
            model.save(MODEL_PATH)
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
    log.info("Loading price rows...")
    df = await load_price_rows(pool)
    log.info("Building sequences...")
    X, y, meta = build_sequences(df)  # noqa: N806
    np.savez_compressed(
        out_path,
        X=X,
        y=y,
        feature_names=np.array(PRICE_FEATURE_COLS),
        horizons=np.array(HORIZONS),
        meta_time=meta["time"].to_numpy().astype("datetime64[ns]"),
        meta_ticker=meta["ticker"].to_numpy().astype(str),
    )
    log.info(f"Saved → {out_path}  (X={X.shape}, y={y.shape})")
    log.info("Load with: d = np.load('dataset.npz', allow_pickle=True)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", metavar="PATH", help="dump dataset to .npz and exit")
    args = parser.parse_args()

    if args.dump:
        asyncio.run(dump(Path(args.dump)))
    else:
        asyncio.run(main())
