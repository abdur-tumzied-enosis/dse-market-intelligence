import numpy as np
from sklearn.isotonic import IsotonicRegression

from ml.inference.predict_prices import calibrated_direction


def _fitted_iso():
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    # higher score -> higher P(up); use float targets so extreme scores map to
    # probabilities strictly between 0 and 1 (avoids degenerate 0.0 / 1.0 edges)
    iso.fit(np.array([-2.0, -1.0, 0.0, 1.0, 2.0]), np.array([0.1, 0.2, 0.5, 0.8, 0.9]))
    return iso


def test_confidence_is_calibrated_prob_not_percentile():
    iso = _fitted_iso()
    scores = np.array([2.0, -2.0])
    dirs, confs = calibrated_direction(scores, iso, low_signal=False)
    assert dirs[0] == "up" and confs[0] > 0.5
    assert dirs[1] == "down" and confs[1] > 0.5        # confidence of the DOWN call
    # not a rank: the top score's confidence is the prob, well below 1.0 here
    assert confs[0] < 1.0


def test_confidence_for_down_call_is_one_minus_p():
    iso = _fitted_iso()
    dirs, confs = calibrated_direction(np.array([-2.0]), iso, low_signal=False)
    p_up = float(iso.predict([-2.0])[0])
    assert dirs[0] == "down"
    assert abs(confs[0] - (1 - p_up)) < 1e-9


def test_low_signal_writes_neutral_half():
    iso = _fitted_iso()
    dirs, confs = calibrated_direction(np.array([2.0, -2.0]), iso, low_signal=True)
    assert list(confs) == [0.5, 0.5]


def test_no_calibrator_writes_neutral_half():
    dirs, confs = calibrated_direction(np.array([2.0, -2.0]), None, low_signal=False)
    assert list(confs) == [0.5, 0.5]
    assert dirs[0] == "up" and dirs[1] == "down"       # direction still by sign
