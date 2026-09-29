"""
Dataset assembly (Phase 4 B7): labels, exclusions, chronological splits, vehicle holdout.

Only columns prefixed f_ are model inputs. meta_ columns identify the row and label_ columns
hold the target and label diagnostics; neither may be used as features.
"""

import hashlib
from typing import Dict, List, Mapping, Tuple

import numpy as np
import pandas as pd

from .features import feature_names
from .labels import label_snapshots

DAY = pd.Timedelta(days=1)


def split_ranges(data_start: pd.Timestamp, splits: Mapping[str, List[int]]) -> Dict[str, Tuple[pd.Timestamp, pd.Timestamp]]:
    """Day ranges (1-based, inclusive) -> half-open [start, end) prediction-time ranges."""
    out = {}
    for name, (d0, d1) in splits.items():
        out[name] = (data_start + (int(d0) - 1) * DAY, data_start + int(d1) * DAY)
    names = sorted(out, key=lambda n: out[n][0])
    for a, b in zip(names, names[1:]):
        if out[a][1] > out[b][0]:
            raise ValueError(f"splits {a} and {b} overlap")
    return out


def assign_split(t: pd.Series, ranges: Mapping[str, Tuple[pd.Timestamp, pd.Timestamp]]) -> pd.Series:
    split = pd.Series(pd.NA, index=t.index, dtype="object")
    for name, (lo, hi) in ranges.items():
        split[(t >= lo) & (t < hi)] = name
    return split


def vehicle_group(vehicle_ids: pd.Series, seed: int, holdout_fraction: float) -> pd.Series:
    cut = int(round(holdout_fraction * 100))

    def g(v: str) -> str:
        h = int(hashlib.sha256(f"{seed}:{v}".encode()).hexdigest(), 16) % 100
        return "holdout" if h < cut else "dev"

    uniq = {v: g(v) for v in pd.unique(vehicle_ids)}
    return vehicle_ids.map(uniq)


def build_dataset(
    features: pd.DataFrame,
    components: pd.DataFrame,
    events: pd.DataFrame,
    data_start: pd.Timestamp,
    data_end: pd.Timestamp,
    splits: Mapping[str, List[int]],
    seed: int,
    holdout_fraction: float,
    horizon_days: float = 7,
) -> Tuple[pd.DataFrame, Dict]:
    """
    features:   offline snapshots (vehicle_id, component, feature_ts, window_complete, feature columns)
    components: vehicle_id, component, vehicle_component_id, vehicle_type
    events:     vehicle_component_id, event_id, event_type, occurred_at
    Returns (dataset rows with meta_/label_/f_ columns and meta_split, exclusion counts).
    """
    df = features.merge(components, on=["vehicle_id", "component"], how="left", validate="many_to_one")
    if df["vehicle_component_id"].isna().any():
        raise ValueError("snapshots without a vehicle_component row")
    df = df[(df["feature_ts"] >= data_start) & (df["feature_ts"] < data_end)].copy()
    lab = label_snapshots(df, events, data_end=data_end, horizon=pd.Timedelta(days=horizon_days))
    df = df.join(lab)

    ranges = split_ranges(data_start, splits)
    df["meta_split"] = assign_split(df["feature_ts"], ranges)
    counts = {
        "snapshots_total": int(len(df)),
        "excluded_warmup": int((~df["window_complete"]).sum()),
        "excluded_in_gap": int((df["window_complete"] & df["in_gap"]).sum()),
        "excluded_tail": int((df["window_complete"] & ~df["in_gap"] & df["tail"]).sum()),
    }
    keep = df["window_complete"] & ~df["in_gap"] & ~df["tail"]
    counts["excluded_outside_splits"] = int((keep & df["meta_split"].isna()).sum())
    df = df[keep & df["meta_split"].notna()].copy()
    counts["rows_kept"] = int(len(df))

    out = pd.DataFrame(index=df.index)
    out["meta_vehicle_id"] = df["vehicle_id"]
    out["meta_component"] = df["component"]
    out["meta_vehicle_type"] = df["vehicle_type"]
    out["meta_vehicle_component_id"] = df["vehicle_component_id"]
    out["meta_feature_ts"] = df["feature_ts"]
    out["meta_vehicle_group"] = vehicle_group(df["vehicle_id"], seed, holdout_fraction)
    out["meta_split"] = df["meta_split"]
    out["label_7d"] = df["label_7d"].astype(np.int8)
    out["label_next_event_id"] = df["label_next_event_id"]
    out["label_hours_to_event"] = df["label_hours_to_event"]
    names = sorted({n for c in df["component"].unique() for n in feature_names(c)})
    feats = pd.DataFrame(
        {f"f_{n}": (df[n].astype(np.float64) if n in df.columns else np.nan) for n in names}, index=df.index
    )
    out = pd.concat([out, feats], axis=1)
    return out.reset_index(drop=True), {"ranges": ranges, **counts}


def model_input_columns(frame: pd.DataFrame, component: str) -> List[str]:
    """The only columns a model may consume: the component's features, f_-prefixed."""
    return [f"f_{n}" for n in feature_names(component) if f"f_{n}" in frame.columns]
