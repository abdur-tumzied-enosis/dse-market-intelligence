import numpy as np
import pandas as pd

from ml.eval.calibrate_direction import fit_horizon_calibrator


def _pairs(n=400, signal=True, seed=0):
    rng = np.random.default_rng(seed)
    t = pd.to_datetime("2023-01-01") + pd.to_timedelta(np.arange(n), unit="D")
    score = rng.normal(0, 1, n)
    if signal:
        p = 1 / (1 + np.exp(-score))            # score truly drives P(up)
        up = (rng.random(n) < p).astype(int)
    else:
        up = (rng.random(n) < 0.5).astype(int)  # independent of score
    return pd.DataFrame({"time": t, "score": score, "actual_up": up})


def test_calibrator_is_monotonic_nondecreasing():
    iso, meta = fit_horizon_calibrator(_pairs(signal=True))
    grid = np.linspace(-3, 3, 50)
    out = iso.predict(grid)
    assert np.all(np.diff(out) >= -1e-9)        # isotonic: never decreasing


def test_calibrator_real_signal_not_low_and_improves_brier():
    iso, meta = fit_horizon_calibrator(_pairs(signal=True, seed=2))
    assert meta["low_signal"] is False
    assert meta["brier"] < 0.25                  # better than the 0.25 coin-flip floor


def test_calibrator_no_signal_flagged_low():
    iso, meta = fit_horizon_calibrator(_pairs(signal=False, seed=3))
    assert meta["low_signal"] is True


def test_fit_raises_on_too_few_pairs():
    import pytest
    with pytest.raises(ValueError, match="too small"):
        fit_horizon_calibrator(_pairs(n=3))


def test_fit_uses_chronological_subsplit_no_overlap():
    # eval half must be strictly later than fit half
    iso, meta = fit_horizon_calibrator(_pairs(signal=True))
    assert pd.Timestamp(meta["fit_end"]) <= pd.Timestamp(meta["eval_start"])
