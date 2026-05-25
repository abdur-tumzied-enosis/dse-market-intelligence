"""XGBoost-based fundamental stock scorer."""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

FEATURE_COLS = [
    "eps_growth_1yr",
    "eps_growth_3yr",
    "nav_growth",
    "pe_vs_sector",
    "div_yield",
    "eps_consistency",
]


class FundamentalScorer:
    """XGBoost classifier: P(stock outperforms peers over next 6 months)."""

    def __init__(self) -> None:
        self._model = XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
        )
        self._trained = False

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._model.fit(X[FEATURE_COLS].fillna(0), y)
        self._trained = True

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self._trained:
            raise RuntimeError("Model not trained. Call fit() or load() first.")
        return self._model.predict_proba(X[FEATURE_COLS].fillna(0))[:, 1]

    def feature_importances(self) -> dict[str, float]:
        return dict(zip(FEATURE_COLS, self._model.feature_importances_.tolist()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self._model, f)

    def load(self, path: Path) -> None:
        with open(path, "rb") as f:
            self._model = pickle.load(f)
        self._trained = True
