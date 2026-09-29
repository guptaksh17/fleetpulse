#!/usr/bin/env python3
"""
Phase 5: per-component models on the Phase 4 dataset.

For each component (BRAKE, POWERTRAIN, BATTERY):
  1. Fit on train: logistic regression baseline (median impute + missingness flags + scaling)
     and HistGradientBoosting (native NaN handling). Both class weighted.
  2. Platt calibration of each model's raw score, fitted on the calibration split only.
  3. Alert threshold chosen on the threshold split (maximum F1 of the calibrated score).
  4. Final test split used once: PR-AUC (headline), ROC-AUC, Brier, precision / recall at the
     threshold, precision at top-K, alerts per 1,000 components per day. Also reported for the
     20 percent vehicle holdout within the test window, and for a prevalence-only baseline.
Only f_ columns are inputs. Served probability is the calibrated value.
"""

import argparse
import json
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ml.model import CalibratedModel, COMPONENTS  # noqa: E402

TOP_K_FRACTION = 0.01


def load(ds_dir: str, comp: str, split: str) -> pd.DataFrame:
    return pd.read_parquet(os.path.join(ds_dir, f"component={comp}", f"split={split}.parquet"))


def usable_features(train: pd.DataFrame) -> list:
    cols = [c for c in train.columns if c.startswith("f_")]
    return [c for c in cols if train[c].notna().any() and train[c].nunique(dropna=True) > 1]


def fit_raw(kind: str, X: pd.DataFrame, y: np.ndarray, seed: int):
    if kind == "logreg":
        m = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                          LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5))
    else:
        m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
                                           min_samples_leaf=200, l2_regularization=1.0,
                                           class_weight="balanced", early_stopping=True,
                                           validation_fraction=0.1, random_state=seed)
    return m.fit(X, y)


def metrics(y: np.ndarray, p: np.ndarray, threshold: float, days: float, n_components: int) -> dict:
    if y.sum() == 0 or y.sum() == len(y):
        return {"n": int(len(y)), "positives": int(y.sum()), "note": "single class"}
    pred = p >= threshold
    tp = int((pred & (y == 1)).sum())
    k = max(1, int(len(y) * TOP_K_FRACTION))
    top = np.argsort(-p)[:k]
    return {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "prevalence": round(float(y.mean()), 5),
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4),
        "brier": round(float(brier_score_loss(y, p)), 5),
        "threshold": round(float(threshold), 4),
        "precision": round(tp / max(1, int(pred.sum())), 4),
        "recall": round(tp / max(1, int(y.sum())), 4),
        "alerts": int(pred.sum()),
        "alerts_per_1000_components_per_day": round(1000.0 * pred.sum() / max(1, n_components) / max(days, 1e-9), 2),
        f"precision_at_top_{TOP_K_FRACTION:.0%}": round(float(y[top].mean()), 4),
    }


def best_f1_threshold(y: np.ndarray, p: np.ndarray) -> float:
    prec, rec, thr = precision_recall_curve(y, p)
    f1 = 2 * prec[:-1] * rec[:-1] / np.maximum(prec[:-1] + rec[:-1], 1e-12)
    return float(thr[int(np.nanargmax(f1))]) if len(thr) else 0.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="data/datasets/ds-f1-l1-v1")
    ap.add_argument("--out", default="data/models/m1")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    manifest = json.load(open(os.path.join(args.dataset, "manifest.json")))
    report = {"dataset_version": manifest["dataset_version"], "feature_schema_version": manifest["feature_schema_version"],
              "model_version": os.path.basename(args.out), "components": {}}

    for comp in COMPONENTS:
        t0 = time.time()
        tr, ca, th, te = (load(args.dataset, comp, s) for s in ("train", "calibration", "threshold", "test"))
        feats = usable_features(tr)
        days_test = (te["meta_feature_ts"].max() - te["meta_feature_ts"].min()).total_seconds() / 86400 + 1 / 24
        n_comp_test = te["meta_vehicle_component_id"].nunique()
        comp_report = {"features_used": len(feats), "rows": {s: int(len(d)) for s, d in zip(("train", "calibration", "threshold", "test"), (tr, ca, th, te))}}
        best = None
        for kind in ("logreg", "hgb"):
            raw = fit_raw(kind, tr[feats], tr["label_7d"].to_numpy(), args.seed)
            model = CalibratedModel(comp, kind, feats, raw).calibrate(ca[feats], ca["label_7d"].to_numpy())
            thr = best_f1_threshold(th["label_7d"].to_numpy(), model.predict_proba(th[feats]))
            model.threshold = thr
            p_te = model.predict_proba(te[feats])
            y_te = te["label_7d"].to_numpy()
            hold = (te["meta_vehicle_group"] == "holdout").to_numpy()
            sel_pr_auc = float(average_precision_score(th["label_7d"].to_numpy(), model.predict_proba(th[feats])))
            comp_report[kind] = {
                "selection_threshold_split_pr_auc": round(sel_pr_auc, 4),
                "calibration_split": metrics(ca["label_7d"].to_numpy(), model.predict_proba(ca[feats]), thr, 5, ca["meta_vehicle_component_id"].nunique()),
                "test": metrics(y_te, p_te, thr, days_test, n_comp_test),
                "test_vehicle_holdout": metrics(y_te[hold], p_te[hold], thr, days_test, te.loc[hold, "meta_vehicle_component_id"].nunique()),
            }
            joblib.dump(model, os.path.join(args.out, f"{comp}_{kind}.joblib"))
            # Model selection uses the threshold split, never the test split.
            if best is None or sel_pr_auc > comp_report[best]["selection_threshold_split_pr_auc"]:
                best = kind
        comp_report["prevalence_baseline_pr_auc"] = round(float(te["label_7d"].mean()), 4)
        comp_report["selected"] = best
        if best == "hgb":
            imp = _hgb_top_features(joblib.load(os.path.join(args.out, f"{comp}_hgb.joblib")), te, feats)
            comp_report["hgb_top_features_permutation"] = imp
        # The served model is the one with the best PR-AUC on the threshold split (test untouched).
        joblib.dump(joblib.load(os.path.join(args.out, f"{comp}_{best}.joblib")), os.path.join(args.out, f"{comp}.joblib"))
        comp_report["seconds"] = round(time.time() - t0, 1)
        report["components"][comp] = comp_report
        print(f"{comp}: " + json.dumps({k: comp_report[k]["test"] for k in ("logreg", "hgb")}))

    with open(os.path.join(args.out, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(f"Saved models and report to {args.out}")


def _hgb_top_features(model, te: pd.DataFrame, feats: list, n: int = 8) -> list:
    """Cheap permutation importance on a test sample (PR-AUC drop)."""
    rng = np.random.default_rng(0)
    s = te.sample(min(len(te), 20000), random_state=0)
    y = s["label_7d"].to_numpy()
    if y.sum() == 0:
        return []
    base = average_precision_score(y, model.predict_proba(s[feats]))
    drops = []
    for c in feats:
        x = s[feats].copy()
        x[c] = rng.permutation(x[c].to_numpy())
        drops.append((c, base - average_precision_score(y, model.predict_proba(x))))
    drops.sort(key=lambda t: -t[1])
    return [{"feature": c, "pr_auc_drop": round(float(d), 4)} for c, d in drops[:n]]


if __name__ == "__main__":
    main()
