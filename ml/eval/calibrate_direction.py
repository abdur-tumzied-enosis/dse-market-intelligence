"""Fit per-horizon isotonic calibrators mapping the LSTM's raw score -> P(up).

Consumes the D1 out-of-sample (score, actual_up) pairs ONLY (never train data),
sub-split chronologically: fit on the earlier half, evaluate Brier/reliability/
low-signal on the later half. Persists {horizon: IsotonicRegression} + metadata.

Run: python -m ml.eval.calibrate_direction
"""
from __future__ import annotations

import asyncio
import logging
import pickle
from pathlib import Path

import pandas as pd
from sklearn.isotonic import IsotonicRegression

from ml.constants import HORIZONS
from ml.eval.lstm_validation import (
    SPLIT_FRAC,
    _build_results,
    oos_pairs,
    select_oos,
)
from ml.eval.metrics import brier_score, directional_hit_rate, is_low_signal, reliability_table

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

CALIBRATORS_PATH = Path("models/v1/lstm_direction_calibrators.pkl")


def fit_horizon_calibrator(pairs: pd.DataFrame) -> tuple[IsotonicRegression, dict]:
    """Chronological 50/50 sub-split of one horizon's OOS pairs. Fit isotonic on
    the earlier half (score -> actual_up), evaluate on the later half. Returns the
    fitted calibrator and a metadata dict (Brier, base/hit rate, low_signal,
    date ranges)."""
    pairs = pairs.sort_values("time").reset_index(drop=True)
    mid = len(pairs) // 2
    fit, ev = pairs.iloc[:mid], pairs.iloc[mid:]

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(fit["score"].to_numpy(), fit["actual_up"].to_numpy())

    p_up = iso.predict(ev["score"].to_numpy())
    actual = ev["actual_up"].to_numpy()
    base = float(actual.mean())
    # directional_hit_rate expects a signed score; calibrated p>=0.5 == 'up'
    hit = directional_hit_rate(p_up - 0.5, actual)
    meta = {
        "n_fit": int(len(fit)),
        "n_eval": int(len(ev)),
        "base_rate": round(base, 4),
        "hit_rate": round(hit, 4),
        "brier": round(brier_score(p_up, actual), 4),
        "low_signal": is_low_signal(hit, base, len(ev)),
        "reliability": reliability_table(p_up, actual).to_dict("records"),
        "fit_end": str(fit["time"].max()),
        "eval_start": str(ev["time"].min()),
    }
    return iso, meta


async def main() -> dict:
    from db.pool import get_batch_pool

    pool = await get_batch_pool()
    results = await _build_results(pool)
    oos = select_oos(results, SPLIT_FRAC, gap_bars=max(HORIZONS))
    pairs_by_h = oos_pairs(oos, HORIZONS)

    calibrators: dict[int, IsotonicRegression] = {}
    metadata: dict[int, dict] = {}
    for h in HORIZONS:
        pairs = pairs_by_h[h]
        if len(pairs) < 100:
            log.warning("h=%dd: only %d OOS pairs — skipping", h, len(pairs))
            continue
        iso, meta = fit_horizon_calibrator(pairs)
        calibrators[h] = iso
        metadata[h] = meta
        log.info(
            "h=%2dd  Brier=%.4f hit=%.3f base=%.3f low_signal=%s",
            h, meta["brier"], meta["hit_rate"], meta["base_rate"], meta["low_signal"],
        )

    CALIBRATORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CALIBRATORS_PATH.open("wb") as f:
        pickle.dump({"calibrators": calibrators, "metadata": metadata}, f)
    log.info("saved -> %s", CALIBRATORS_PATH)
    return metadata


if __name__ == "__main__":
    asyncio.run(main())
