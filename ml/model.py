"""Calibrated model wrapper shared by training, batch scoring, the live scorer and the API."""

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

COMPONENTS = ("BRAKE", "POWERTRAIN", "BATTERY")


class CalibratedModel:
    """Raw classifier + Platt scaling fitted on a separate calibration split."""

    def __init__(self, component: str, kind: str, features: list, raw):
        self.component = component
        self.kind = kind
        self.features = list(features)
        self.raw = raw
        self.platt = None
        self.threshold = 0.5

    def _score(self, X: pd.DataFrame) -> np.ndarray:
        p = np.clip(self.raw.predict_proba(X[self.features])[:, 1], 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def calibrate(self, X: pd.DataFrame, y: np.ndarray) -> "CalibratedModel":
        self.platt = LogisticRegression(C=1e6, max_iter=1000).fit(self._score(X), y)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.platt.predict_proba(self._score(X))[:, 1]
