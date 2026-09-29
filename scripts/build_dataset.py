#!/usr/bin/env python3
"""
Dataset builder (Phase 4 B7). Reads the offline feature snapshots and maintenance_event,
labels every snapshot (fleetpulse_features.labels), applies the exclusions and the
chronological splits from configs/dataset.yaml, and writes

  data/datasets/<dataset_version>/component=<C>/split=<S>.parquet
  data/datasets/<dataset_version>/manifest.json

Only f_ columns are model inputs. Prints the manifest table and the calibration guardrail.
"""

import argparse
from datetime import timedelta
import json
import os
import sys
import time

import pandas as pd
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fleetpulse_features.dataset import build_dataset, model_input_columns
from fleetpulse_features.features import feature_names
from fleetpulse_features.labels import LABEL_DEFINITION_VERSION
from simulator.config import load_config

COMPONENTS_SQL = """
SELECT vc.vehicle_id::text AS vehicle_id, vc.component, vc.vehicle_component_id::text AS vehicle_component_id,
       v.vehicle_type
FROM vehicle_component vc JOIN vehicle v ON v.vehicle_id = vc.vehicle_id
"""
EVENTS_SQL = """
SELECT vehicle_component_id::text AS vehicle_component_id, event_id::text AS event_id, event_type, occurred_at
FROM maintenance_event
"""


def main():
    import psycopg2

    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/dataset.yaml")
    p.add_argument("--postgres", default=os.getenv("POSTGRES_DSN", "host=localhost port=5434 dbname=fleetpulse user=fleetpulse password=fleetpulse"))
    args = p.parse_args()
    t0 = time.time()

    with open(args.config) as f:
        dcfg = yaml.safe_load(f)
    sim = load_config(dcfg["simulator_config"])
    data_start = pd.Timestamp(sim.clock.start_time)
    data_end = data_start + pd.Timedelta(days=sim.clock.days)

    feats = pd.read_parquet(dcfg["features_path"])
    feats["feature_ts"] = pd.to_datetime(feats["feature_ts_ms"], unit="ms", utc=True)
    schema_versions = sorted(feats["feature_schema_version"].unique().tolist())
    if len(schema_versions) != 1:
        raise SystemExit(f"mixed feature schema versions: {schema_versions}")

    with psycopg2.connect(args.postgres) as pg:
        comps = pd.read_sql(COMPONENTS_SQL, pg)
        events = pd.read_sql(EVENTS_SQL, pg)
    events["occurred_at"] = pd.to_datetime(events["occurred_at"], utc=True)

    ds, info = build_dataset(
        feats, comps, events, data_start, data_end, dcfg["splits"], int(dcfg["seed"]),
        float(dcfg["holdout_fraction"]), float(dcfg["horizon_days"]),
    )

    out_dir = os.path.join(dcfg["output_dir"], dcfg["dataset_version"])
    os.makedirs(out_dir, exist_ok=True)
    table = {}
    warnings = []
    for comp in ("BRAKE", "POWERTRAIN", "BATTERY"):
        cols = [c for c in ds.columns if not c.startswith("f_")] + model_input_columns(ds, comp)
        table[comp] = {}
        for split in dcfg["splits"]:
            part = ds[(ds["meta_component"] == comp) & (ds["meta_split"] == split)][cols]
            d = os.path.join(out_dir, f"component={comp}")
            os.makedirs(d, exist_ok=True)
            part.to_parquet(os.path.join(d, f"split={split}.parquet"), index=False)
            pos = int(part["label_7d"].sum())
            table[comp][split] = {
                "rows": int(len(part)),
                "positives": pos,
                "prevalence": round(pos / len(part), 5) if len(part) else None,
                "distinct_positive_events": int(part.loc[part["label_7d"] == 1, "label_next_event_id"].nunique()),
                "vehicles": int(part["meta_vehicle_id"].nunique()),
                "holdout_rows": int((part["meta_vehicle_group"] == "holdout").sum()),
            }
        cal = table[comp]["calibration"]
        g = dcfg["guardrails"]
        if cal["positives"] < g["min_calibration_positive_snapshots"]:
            warnings.append(f"{comp}: calibration split has {cal['positives']} positive snapshots (< {g['min_calibration_positive_snapshots']})")
        if cal["distinct_positive_events"] < g["min_calibration_positive_events"]:
            warnings.append(f"{comp}: calibration split has {cal['distinct_positive_events']} distinct positive events (< {g['min_calibration_positive_events']})")

    manifest = {
        "dataset_version": dcfg["dataset_version"],
        "seed": dcfg["seed"],
        "simulator_config": dcfg["simulator_config"],
        "simulator_config_hash": sim.config_hash,
        "feature_schema_version": schema_versions[0],
        "label_definition_version": LABEL_DEFINITION_VERSION,
        "label_definition": "label_7d = 1 if MAINTENANCE_REQUIRED or FAILURE of the component occurs in (t, t + 7 d]",
        "exclusions": "window_complete = false (warm-up); in-gap (awaiting service at t); tail (t + 7 d > data end); outside split ranges (buffers)",
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "split_days": dcfg["splits"],
        "split_ranges": {k: [v[0].isoformat(), v[1].isoformat()] for k, v in info["ranges"].items()},
        "holdout": {"fraction": dcfg["holdout_fraction"], "rule": "sha256(f'{seed}:{vehicle_id}') % 100 < 20 -> holdout"},
        "model_input_columns": {c: model_input_columns(ds, c) for c in ("BRAKE", "POWERTRAIN", "BATTERY")},
        "non_feature_column_prefixes": ["meta_", "label_"],
        "exclusion_counts": {k: v for k, v in info.items() if k != "ranges"},
        "counts": table,
        "guardrail_warnings": warnings,
        "build_seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    print(f"Dataset {dcfg['dataset_version']} -> {out_dir}")
    print(f"feature schema {manifest['feature_schema_version']}, labels {manifest['label_definition_version']}, simulator config {sim.config_hash[:12]}")
    print("Exclusions:", json.dumps(manifest["exclusion_counts"]))
    print()
    print(f"{'component':<11} {'split':<12} {'rows':>8} {'positives':>10} {'prevalence':>11} {'pos_events':>11} {'vehicles':>9} {'holdout':>8}")
    for comp, splits in table.items():
        for split, r in splits.items():
            prev = f"{100 * r['prevalence']:.2f}%" if r["prevalence"] is not None else "n/a"
            print(f"{comp:<11} {split:<12} {r['rows']:>8} {r['positives']:>10} {prev:>11} {r['distinct_positive_events']:>11} {r['vehicles']:>9} {r['holdout_rows']:>8}")
    print()
    if warnings:
        print("CALIBRATION GUARDRAIL WARNINGS:")
        for w in warnings:
            print("  WARNING:", w)
    else:
        print("Calibration guardrail: every component has >= 50 positive snapshots and >= 5 distinct positive events in the calibration split.")


if __name__ == "__main__":
    main()
