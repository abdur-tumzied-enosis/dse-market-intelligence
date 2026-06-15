"""Point-in-time validation of the cross-sectional rank-return LSTM, reframed for
the per-stock direction + confidence the UI ships.

For each horizon, on a leak-free out-of-sample slice, reports three framings:
  1. rank-IC        — does the relative ranking signal exist? (model's design)
  2. directional hit-rate vs base rate — is the absolute up/down call > 50%?
  3. calibration    — Brier + reliability of the to-be-shipped confidence.
Split full vs ex-floor-price regime (2022-07..2024-01), whose returns were frozen
by BSEC and poison return-based signal.

Run: python -m ml.eval.lstm_validation
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from db.pool import get_batch_pool
from ml.constants import HORIZONS
from ml.eval.metrics import (
    brier_score,
    directional_hit_rate,
    rank_ic,
    reliability_table,
)
from ml.features.sequence_builder import (
    build_sequences,
    date_boundary,
    load_price_rows,
    purged_val_start,
    trading_calendar,
)
from ml.models.lstm_predictor import LSTMPredictor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

MODEL_PATH = Path("models/v1/lstm_v0.pt")
FLOOR_START = date(2022, 7, 28)
FLOOR_END = date(2024, 1, 22)
SPLIT_FRAC = 0.8


def select_oos(results: pd.DataFrame, frac: float, gap_bars: int) -> pd.DataFrame:
    """Keep only the purged validation tail: dates >= purged_val_start, where the
    train/val cut is a date boundary and the purge advances gap_bars trading bars."""
    times = results["time"].to_numpy()
    cal = trading_calendar(times)
    boundary = date_boundary(times, frac)
    start = purged_val_start(cal, boundary, gap_bars)
    return results[results["time"] >= start].reset_index(drop=True)


def split_regime(results: pd.DataFrame, floor_start: date, floor_end: date) -> pd.DataFrame:
    """Drop samples whose entry date falls inside the floor-price window. (Entry
    date is a conservative proxy for forward-window overlap; documented in spec.)"""
    t = results["time"]
    keep = (t < pd.Timestamp(floor_start)) | (t > pd.Timestamp(floor_end))
    return results[keep].reset_index(drop=True)


def evaluate_results(results: pd.DataFrame, horizons: list[int]) -> dict:
    """Per-horizon rank-IC (per date, averaged), directional hit-rate vs base
    rate, and calibration (Brier + reliability) over the given long results frame."""
    out: dict = {"n_samples": int(len(results)), "horizons": {}}
    for h in horizons:
        pcol, fcol = f"pred_{h}", f"fwd_ret_{h}"
        sub = results.dropna(subset=[pcol, fcol])
        if sub.empty:
            continue
        ics = [
            rank_ic(g[pcol].to_numpy(), g[fcol].to_numpy())
            for _, g in sub.groupby("time")
        ]
        ics = [v for v in ics if np.isfinite(v)]
        actual_up = (sub[fcol].to_numpy() > 0).astype(int)
        hit = directional_hit_rate(sub[pcol].to_numpy(), actual_up)
        base = float(actual_up.mean())
        # confidence proxy = min-max of pred into [0,1] for a pre-calibration Brier read
        s = sub[pcol].to_numpy()
        rng = s.max() - s.min()
        conf = (s - s.min()) / rng if rng > 0 else np.full_like(s, 0.5)
        out["horizons"][h] = {
            "rank_ic": {
                "n": len(ics),
                "mean_ic": round(float(np.mean(ics)), 4) if ics else float("nan"),
                "pct_positive": round(float(np.mean(np.array(ics) > 0)), 3) if ics else float("nan"),
            },
            "hit_rate": round(hit, 4),
            "base_rate": round(base, 4),
            "brier": round(brier_score(conf, actual_up), 4),
            "reliability": reliability_table(conf, actual_up).to_dict("records"),
        }
    return out


async def _build_results(pool) -> pd.DataFrame:
    """Load model + all historical windows, run inference, return a long results
    frame [time, ticker, pred_h..., fwd_ret_h...]."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"No model at {MODEL_PATH}. Run `python -m ml.train.train_lstm` first."
        )
    df = await load_price_rows(pool)
    X, _y, meta = build_sequences(df)  # noqa: N806
    model = LSTMPredictor.load(MODEL_PATH)
    preds = model.predict(torch.from_numpy(X)).numpy()  # (N, n_horizons)
    res = meta[["time", "ticker"]].copy()
    res["time"] = pd.to_datetime(res["time"])
    for hi, h in enumerate(HORIZONS):
        res[f"pred_{h}"] = preds[:, hi]
        res[f"fwd_ret_{h}"] = meta[f"fwd_ret_{h}"].to_numpy()
    return res


def oos_pairs(results_oos: pd.DataFrame, horizons: list[int]) -> dict[int, pd.DataFrame]:
    """Per-horizon [time, score, actual_up] frames for the calibrator (D2)."""
    pairs: dict[int, pd.DataFrame] = {}
    for h in horizons:
        pcol, fcol = f"pred_{h}", f"fwd_ret_{h}"
        sub = results_oos.dropna(subset=[pcol, fcol])
        pairs[h] = pd.DataFrame(
            {
                "time": sub["time"].to_numpy(),
                "score": sub[pcol].to_numpy(),
                "actual_up": (sub[fcol].to_numpy() > 0).astype(int),
            }
        )
    return pairs


def _print_report(tag: str, rep: dict) -> None:
    log.info("=" * 64)
    log.info("LSTM DIRECTION VALIDATION [%s]  n=%d", tag, rep["n_samples"])
    for h, blk in rep["horizons"].items():
        log.info(
            "  h=%2dd  IC=%-7s pos%%=%-5s | hit=%.3f base=%.3f | Brier=%.4f",
            h, blk["rank_ic"]["mean_ic"], blk["rank_ic"]["pct_positive"],
            blk["hit_rate"], blk["base_rate"], blk["brier"],
        )


async def main() -> dict:
    pool = await get_batch_pool()
    results = await _build_results(pool)
    log.info("results rows=%d (survivorship: all tickers with history)", len(results))
    oos = select_oos(results, SPLIT_FRAC, gap_bars=max(HORIZONS))
    full = evaluate_results(oos, HORIZONS)
    ex_floor = evaluate_results(split_regime(oos, FLOOR_START, FLOOR_END), HORIZONS)
    _print_report("full", full)
    _print_report("ex_floor", ex_floor)
    return {"full": full, "ex_floor": ex_floor}


if __name__ == "__main__":
    asyncio.run(main())
