"""Phase 5 model utilities: calibration, thresholding, metrics, feature selection."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ml.model import CalibratedModel  # noqa: E402
from ml import train  # noqa: E402


def synthetic(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    logit = -3.5 + 1.8 * x1
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    X = pd.DataFrame({"f_signal": x1, "f_noise": x2, "f_const": 1.0, "f_empty": np.nan})
    X.loc[rng.random(n) < 0.1, "f_signal"] = np.nan
    return X, y


class TestMl(unittest.TestCase):
    def test_usable_features_drops_constant_and_empty(self):
        X, y = synthetic()
        self.assertEqual(sorted(train.usable_features(X)), ["f_noise", "f_signal"])

    def test_models_beat_prevalence_and_calibration_is_reasonable(self):
        X, y = synthetic(12000)
        tr, ca, te = slice(0, 6000), slice(6000, 9000), slice(9000, 12000)
        feats = ["f_signal", "f_noise"]
        for kind in ("logreg", "hgb"):
            raw = train.fit_raw(kind, X.iloc[tr][feats], y[tr], seed=1)
            m = CalibratedModel("BRAKE", kind, feats, raw).calibrate(X.iloc[ca][feats], y[ca])
            p = m.predict_proba(X.iloc[te])
            self.assertTrue(((p >= 0) & (p <= 1)).all())
            met = train.metrics(y[te], p, 0.2, days=3, n_components=100)
            self.assertGreater(met["pr_auc"], 2 * y[te].mean(), kind)
            # Platt calibration: mean predicted probability close to the observed rate
            self.assertLess(abs(p.mean() - y[te].mean()), 0.03, kind)

    def test_best_f1_threshold_and_metric_fields(self):
        y = np.array([0, 0, 0, 1, 1, 0, 1, 0])
        p = np.array([0.1, 0.2, 0.3, 0.8, 0.7, 0.4, 0.9, 0.05])
        thr = train.best_f1_threshold(y, p)
        self.assertAlmostEqual(thr, 0.7)
        m = train.metrics(y, p, thr, days=1, n_components=4)
        self.assertEqual((m["precision"], m["recall"], m["alerts"]), (1.0, 1.0, 3))
        self.assertEqual(train.metrics(np.zeros(3, int), np.zeros(3), 0.5, 1, 1)["note"], "single class")


if __name__ == "__main__":
    unittest.main()
