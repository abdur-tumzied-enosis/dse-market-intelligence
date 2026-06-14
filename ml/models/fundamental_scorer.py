"""XGBoost-based fundamental stock scorer with isotonic calibration."""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

FEATURE_COLS = [
    # Growth
    "eps_growth_1yr", "eps_growth_3yr", "profit_cagr_3y", "profit_cagr_5y",
    "nav_growth", "quarterly_eps_yoy",
    # Quality
    "eps_consistency", "roe", "earnings_quality",
    # Value
    "pe_vs_sector", "pb_ratio", "div_yield", "dividend_yield_pct",
    # Dividends
    "dividend_streak", "cash_div_ratio_5y", "payout_ratio",
    # Safety (model)
    "rights_count_10y",
    # Ownership
    "inst_flow_pp", "foreign_flow_pp", "institution_pct", "foreign_pct",
]

_CALIB_MIN_ROWS = 30
_CALIB_FRACTION = 0.2


class FundamentalScorer:
    """XGBoost classifier (isotonic-calibrated): P(stock outperforms peers, 12m)."""

    def __init__(self) -> None:
        self._model = xgb.XGBClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
            reg_lambda=1.0, eval_metric="logloss", random_state=42,
        )
        self._calibrator: IsotonicRegression | None = None
        self._medians: pd.Series = pd.Series(0.0, index=FEATURE_COLS)
        self.feature_cols: list[str] = list(FEATURE_COLS)
        self._trained = False

    def _prep(self, X: pd.DataFrame) -> pd.DataFrame:
        return X[self.feature_cols].fillna(self._medians)

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._medians = X[FEATURE_COLS].median().fillna(0.0)
        Xf = self._prep(X)
        n = len(Xf)
        cut = int(n * (1 - _CALIB_FRACTION))
        X_fit, y_fit = Xf.iloc[:cut], y.iloc[:cut]
        X_cal, y_cal = Xf.iloc[cut:], y.iloc[cut:]

        can_calibrate = (
            n >= _CALIB_MIN_ROWS
            and y_fit.nunique() == 2
            and y_cal.nunique() == 2
            and len(y_cal) >= 10
        )
        if can_calibrate:
            self._model.fit(X_fit, y_fit)
            raw_cal = self._model.predict_proba(X_cal)[:, 1]
            self._calibrator = IsotonicRegression(out_of_bounds="clip")
            self._calibrator.fit(raw_cal, y_cal)
        else:
            self._model.fit(Xf, y)
            self._calibrator = None
        self._trained = True

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self._trained:
            raise RuntimeError("Model not trained. Call fit() or load() first.")
        Xf = self._prep(X)
        raw = self._model.predict_proba(Xf)[:, 1]
        if self._calibrator is not None:
            return self._calibrator.predict(raw)
        return raw

    def shap_contributions(self, X: pd.DataFrame) -> pd.DataFrame:
        """Native XGBoost TreeSHAP contributions (drops the bias column).

        Values are from the raw (uncalibrated) booster output, not the isotonic-calibrated probabilities.
        """
        if not self._trained:
            raise RuntimeError("Model not trained. Call fit() or load() first.")
        Xf = self._prep(X)
        booster = self._model.get_booster()
        dmat = xgb.DMatrix(Xf)
        contribs = booster.predict(dmat, pred_contribs=True)
        return pd.DataFrame(contribs[:, :-1], columns=self.feature_cols, index=X.index)

    def feature_importances(self) -> dict[str, float]:
        if not self._trained:
            raise RuntimeError("Model not trained. Call fit() or load() first.")
        return dict(zip(self.feature_cols, self._model.feature_importances_.tolist()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "model": self._model, "calibrator": self._calibrator,
                "medians": self._medians, "feature_cols": self.feature_cols,
            }, f)

    def load(self, path: Path) -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        if isinstance(data, dict):
            self._model = data["model"]
            self._calibrator = data.get("calibrator")
            self._medians = data.get("medians", pd.Series(0.0, index=FEATURE_COLS))
            self.feature_cols = data.get("feature_cols", list(FEATURE_COLS))
        else:
            self._model = data
            self._calibrator = None
            self._medians = pd.Series(0.0, index=FEATURE_COLS)
            self.feature_cols = list(FEATURE_COLS)
        self._trained = True
