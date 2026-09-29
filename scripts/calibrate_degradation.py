#!/usr/bin/env python3
"""
Degradation calibration for the FleetPulse simulator (Phase 3, reworked in Phase 4 Part A2).

Runs the shared FleetEngine in memory and reports:
  1. Per-signal univariate ROC-AUC of the latest hourly value of each raw observable
     signal against label_7d (MAINTENANCE_REQUIRED or FAILURE in (t, t + 7 d]),
     per component, excluding in-gap and unlabeled-tail snapshots.
     Target: no single signal above 0.85 (AUC reported as max(AUC, 1 - AUC)).
  2. Hourly positive rate per component (target 3 to 8 percent).
  3. Event prevalence per vehicle-component (target 25 to 40 percent over the run) and
     per vehicle (informational), and the sudden-failure ratio (target below 15 percent).
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import sys
import time
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.config import load_config, SimConfig
from simulator.engine import FleetEngine
from fleetpulse_features.labels import label_snapshots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("calibrate-degradation")

# Raw observable signals: name -> OEM-A payload key
SIGNALS = {
    "speed_kmh": "vehicleSpeed",
    "acceleration_ms2": "longitudinalAccel",
    "engine_temp_c": "engineTemp",
    "motor_temp_c": "motorTemperature",
    "engine_load_pct": "engineLoadPct",
    "rpm": "engineRpm",
    "power_kw": "powerKw",
    "battery_temp_c": "batteryTemp",
    "voltage_v": "batteryVoltage",
    "current_a": "batteryCurrent",
    "soc_pct": "batteryLevel",
}
COMPONENTS = ("BRAKE", "POWERTRAIN", "BATTERY")
AUC_LIMIT = 0.85


def simulate(config: SimConfig, days: int, collect_snapshots: bool = True):
    """
    Runs the engine for `days` and returns (events, snapshots, engine).
    events: list of MaintenanceEventRecord in emission order.
    snapshots: DataFrame with one row per (vehicle_component, hourly t) holding the latest
               value of each signal from telemetry with ts < t.
    """
    engine = FleetEngine(config)
    end_time = engine.start_time + timedelta(days=days)
    events: List = []
    latest: Dict[str, Dict[str, float]] = {}
    rows = []
    comp_index = [
        (v.vehicle_id, v.vehicle_type, comp, v.vehicle_component_ids[comp])
        for v in engine.vehicles
        for comp in COMPONENTS
        if comp in v.components
    ]

    def on_step(t, outputs):
        # Snapshot at the top of every hour uses only values emitted before t.
        if collect_snapshots and t.minute == 0 and t.second == 0:
            for vid, vtype, comp, vcid in comp_index:
                vals = latest.get(vid)
                if vals is None:
                    continue
                rows.append((vid, vtype, comp, vcid, t, *[vals[s] for s in SIGNALS], vals["dtc_count"]))
        for out in outputs:
            p = out.payload
            vals = {s: (np.nan if p.get(k) is None else float(p[k])) for s, k in SIGNALS.items()}
            vals["dtc_count"] = float(len(p.get("faultCodes") or []))
            latest[out.vehicle.vehicle_id] = vals
            events.extend(out.events)

    engine.run(end_time, on_step)
    snapshots = pd.DataFrame(
        rows,
        columns=["vehicle_id", "vehicle_type", "component", "vehicle_component_id", "feature_ts", *SIGNALS, "dtc_count"],
    )
    return events, snapshots, engine


def collect_events(config: SimConfig, days: int) -> List[dict]:
    """Events only (used by the offline-versus-calibration equality test)."""
    events, _, _ = simulate(config, days, collect_snapshots=False)
    return [e.to_dict() for e in events]


def roc_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Mann-Whitney AUC with average ranks for ties. Returns NaN if a class is empty."""
    n_pos = int(labels.sum())
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    return float((ranks[labels == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def run_calibration(config_path: str, days: int, vehicles_count: int) -> dict:
    config = load_config(config_path)
    config.population.vehicles = vehicles_count
    t0 = time.time()
    events, snaps, engine = simulate(config, days)
    sim_seconds = time.time() - t0
    end_time = engine.start_time + timedelta(days=days)

    ev_df = pd.DataFrame([e.to_dict() for e in events])
    if ev_df.empty:
        ev_df = pd.DataFrame(columns=["vehicle_id", "vehicle_component_id", "component", "event_id", "event_type", "occurred_at"])
    lab = label_snapshots(snaps, ev_df, data_end=end_time)
    snaps = snaps.join(lab)
    eligible = snaps[~snaps["in_gap"] & ~snaps["tail"]]

    counts = ev_df["event_type"].value_counts().to_dict() if len(ev_df) else {}
    trig = ev_df[ev_df["event_type"].isin(["MAINTENANCE_REQUIRED", "FAILURE"])] if len(ev_df) else ev_df
    sudden = int((trig["metadata"].apply(lambda m: (m or {}).get("failure_type") == "SUDDEN_FAILURE")).sum()) if len(trig) else 0

    per_component = {}
    for comp in COMPONENTS:
        n_active = sum(1 for v in engine.vehicles if comp in v.components)
        e_sub = eligible[eligible["component"] == comp]
        t_sub = trig[trig["component"] == comp] if len(trig) else trig
        aucs = {}
        for sig in [*SIGNALS, "dtc_count"]:
            m = e_sub[sig].notna().to_numpy()
            y = e_sub["label_7d"].to_numpy()[m]
            x = e_sub[sig].to_numpy()[m]
            a = roc_auc(x, y)
            aucs[sig] = {
                "auc": None if np.isnan(a) else round(max(a, 1 - a), 4),
                "direction": None if np.isnan(a) else ("higher" if a >= 0.5 else "lower"),
                "n": int(m.sum()),
            }
        per_component[comp] = {
            "active_components": n_active,
            "trigger_events": int(len(t_sub)),
            "components_with_event": int(t_sub["vehicle_component_id"].nunique()) if len(t_sub) else 0,
            "component_prevalence_pct": round(100.0 * (t_sub["vehicle_component_id"].nunique() if len(t_sub) else 0) / max(1, n_active), 2),
            "eligible_snapshots": int(len(e_sub)),
            "positive_snapshots": int(e_sub["label_7d"].sum()),
            "hourly_positive_rate_pct": round(100.0 * e_sub["label_7d"].mean(), 3) if len(e_sub) else None,
            "signal_auc": aucs,
        }

    # Stationarity: trigger events per third of the run.
    thirds = {}
    if len(trig):
        occ = pd.to_datetime(trig["occurred_at"], utc=True)
        span = (end_time - engine.start_time) / 3
        for k in range(3):
            lo = pd.Timestamp(engine.start_time + k * span)
            hi = pd.Timestamp(engine.start_time + (k + 1) * span)
            thirds[f"days_{k * days // 3}_{(k + 1) * days // 3}"] = int(((occ >= lo) & (occ < hi)).sum())

    all_active = sum(pc["active_components"] for pc in per_component.values())
    all_with = sum(pc["components_with_event"] for pc in per_component.values())
    vehicles_with_event = trig["vehicle_id"].nunique() if len(trig) else 0
    max_auc = max(
        (s["auc"] for pc in per_component.values() for s in pc["signal_auc"].values() if s["auc"] is not None),
        default=float("nan"),
    )
    report = {
        "config": config_path,
        "config_hash": config.config_hash,
        "seed": config.seed,
        "vehicles": vehicles_count,
        "days": days,
        "start_time": engine.start_time.isoformat(),
        "sim_wall_seconds": round(sim_seconds, 1),
        "event_counts": {k: int(v) for k, v in counts.items()},
        "sudden_failures": sudden,
        "sudden_ratio_pct": round(100.0 * sudden / max(1, len(trig)), 2),
        "component_prevalence_pct": round(100.0 * all_with / max(1, all_active), 2),
        "vehicle_prevalence_pct": round(100.0 * vehicles_with_event / max(1, vehicles_count), 2),
        "max_single_signal_auc": max_auc,
        "per_component": per_component,
        "trigger_events_by_third": thirds,
        "degradation": config.to_dict()["degradation"],
    }
    report["checks"] = {
        "max_single_signal_auc_le_0.85": bool(max_auc <= AUC_LIMIT),
        "component_prevalence_25_40": bool(25.0 <= report["component_prevalence_pct"] <= 40.0),
        "component_prevalence_25_40_by_type": {
            c: bool(25.0 <= pc["component_prevalence_pct"] <= 40.0) for c, pc in per_component.items()
        },
        "sudden_ratio_lt_15": bool(report["sudden_ratio_pct"] < 15.0),
        "hourly_positive_rate_3_8": {
            c: (pc["hourly_positive_rate_pct"] is not None and 3.0 <= pc["hourly_positive_rate_pct"] <= 8.0)
            for c, pc in per_component.items()
        },
    }
    return report


def render_markdown(r: dict) -> str:
    def status(ok):
        return "PASS" if ok else "FAIL"

    lines = [
        "# FleetPulse Phase 3: Simulator Calibration Report",
        "",
        f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} by `scripts/calibrate_degradation.py`",
        f"- Config: `{r['config']}` (hash {r['config_hash'][:12]}), seed {r['seed']}",
        f"- Fleet: {r['vehicles']} vehicles, {r['days']} simulated days from {r['start_time']}",
        f"- Simulation wall time: {r['sim_wall_seconds']} s",
        "",
        "Method: hourly snapshots per active vehicle component. Each snapshot holds the latest value of each",
        "raw signal emitted strictly before t. label_7d = 1 if a MAINTENANCE_REQUIRED or FAILURE event of that",
        "component occurs in (t, t + 7 d]. Snapshots taken while the component awaits service (in-gap) and",
        "snapshots whose 7-day horizon extends past the end of the run are excluded. AUC is reported as",
        "max(AUC, 1 - AUC) with the direction of the association.",
        "",
        "## 1. Targets",
        "",
        "| Check | Target | Value | Status |",
        "|---|---|---|---|",
        f"| Max single-signal ROC-AUC | <= 0.85 | {r['max_single_signal_auc']:.4f} | {status(r['checks']['max_single_signal_auc_le_0.85'])} |",
        f"| Prevalence per vehicle-component (all) | 25 - 40 % | {r['component_prevalence_pct']} % | {status(r['checks']['component_prevalence_25_40'])} |",
        f"| Sudden failure ratio | < 15 % | {r['sudden_ratio_pct']} % | {status(r['checks']['sudden_ratio_lt_15'])} |",
    ]
    for c, pc in r["per_component"].items():
        ok = r["checks"]["component_prevalence_25_40_by_type"][c]
        lines.append(f"| Prevalence {c} components | 25 - 40 % | {pc['component_prevalence_pct']} % | {status(ok)} |")
    for c, pc in r["per_component"].items():
        ok = r["checks"]["hourly_positive_rate_3_8"][c]
        lines.append(f"| Hourly positive rate {c} | 3 - 8 % | {pc['hourly_positive_rate_pct']} % | {'PASS' if ok else 'OUTSIDE'} |")
    lines += [
        f"| Prevalence per vehicle (informational) | n/a | {r['vehicle_prevalence_pct']} % | n/a |",
        "",
        "## 2. Events",
        "",
        f"- Event counts: {json.dumps(r['event_counts'], sort_keys=True)}",
        f"- Sudden failures: {r['sudden_failures']}",
        f"- MAINTENANCE_REQUIRED + FAILURE events per third of the run (stationarity): {json.dumps(r['trigger_events_by_third'])}",
        "",
        "| Component | Active | With event | Prevalence % | Trigger events | Eligible snapshots | Positive snapshots | Positive rate % |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c, pc in r["per_component"].items():
        lines.append(
            f"| {c} | {pc['active_components']} | {pc['components_with_event']} | {pc['component_prevalence_pct']} | "
            f"{pc['trigger_events']} | {pc['eligible_snapshots']} | {pc['positive_snapshots']} | {pc['hourly_positive_rate_pct']} |"
        )
    lines += ["", "## 3. Per-signal univariate ROC-AUC (latest hourly value)", ""]
    sigs = list(next(iter(r["per_component"].values()))["signal_auc"].keys())
    lines.append("| Signal | " + " | ".join(r["per_component"].keys()) + " |")
    lines.append("|---|" + "---|" * len(r["per_component"]))
    for s in sigs:
        cells = []
        for pc in r["per_component"].values():
            a = pc["signal_auc"][s]
            cells.append("n/a" if a["auc"] is None else f"{a['auc']:.4f} ({a['direction']})")
        lines.append(f"| {s} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 4. Degradation parameters used",
        "",
        "```json",
        json.dumps(r["degradation"], indent=2, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="FleetPulse Degradation Calibration")
    parser.add_argument("--config", type=str, default="configs/offline_train.yaml")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--vehicles", type=int, default=200)
    parser.add_argument("--out-report", type=str, default="docs/phase3-calibration.md")
    parser.add_argument("--out-json", type=str, default="")
    args = parser.parse_args()

    report = run_calibration(args.config, args.days, args.vehicles)
    md = render_markdown(report)
    print(md)
    if args.out_report:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_report)), exist_ok=True)
        with open(args.out_report, "w", encoding="utf-8") as f:
            f.write(md)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)


if __name__ == "__main__":
    main()
