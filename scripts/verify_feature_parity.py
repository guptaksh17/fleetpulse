#!/usr/bin/env python3
"""
Streaming versus offline feature parity (Phase 4).

Replays N vehicles x D days of telemetry from TimescaleDB through the stream processor's
batch pipeline (stream_processor.pipeline.process_batch: dedup check, Lua dedup+feature
update, Bloom, snapshot step) against a real Redis, and compares every snapshot with the
offline reference (scripts/build_features_offline.py):
  counts exact, floats within 1e-6 relative, NaN matching NaN, window_complete equal,
  identical (vehicle, component, feature_ts) key sets.

Three streaming variants must also produce identical output to each other:
  clean      each event delivered once, Kafka-sized batches in time order
  dup15      15 percent of events redelivered 0 to 3 batches later (at-least-once replay)
  restart    mid-run crash after the Lua step but before the snapshot step and before the
             offset commit; a fresh process (new engine, cold Bloom, new Redis connection)
             replays the uncommitted batch and continues

Streaming state uses isolated Redis prefixes (fsparity:, dedupparity:) so live keys are
never touched. Also reports Redis memory per vehicle.
"""

import argparse
from datetime import timedelta
import json
import math
import os
import random
import sys
import time
from typing import Dict, List, Tuple

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "stream-processor"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from dataclasses import replace

from fleetpulse_features.config import load_feature_config
from fleetpulse_features.features import feature_names
from stream_processor.dedup.stage import DedupStage
from stream_processor.features.context_cache import StaticContextProvider
from stream_processor.features.engine import FeatureEngine
from stream_processor.pipeline import process_batch
from build_features_offline import build_offline_features, fetch_telemetry
from warm_feature_state import records_from_frame

FS_PREFIX = "fsparity"
DEDUP_PREFIX = "dedupparity"
REL_TOL = 1e-6
INTEGER_FEATURE_MARKERS = ("events", "harsh_brakes", "harsh_high_speed", "has_service_history", "vt_")


def is_integer_feature(name: str) -> bool:
    return any(m in name for m in INTEGER_FEATURE_MARKERS) and not name.endswith(("_mean", "_std", "_max", "_min"))


def clear_state(r, vids):
    for vid in vids:
        for pattern in (f"{FS_PREFIX}:{vid}:*", f"{DEDUP_PREFIX}:{vid}:*"):
            keys = list(r.scan_iter(match=pattern, count=1000))
            for i in range(0, len(keys), 1000):
                r.delete(*keys[i: i + 1000])


def make_process(redis_factory, fcfg, context, collected: Dict):
    r = redis_factory()

    def writer(rows):
        for vid, comp, t_ms, schema, complete, feats in rows:
            collected.setdefault((vid, comp, int(t_ms)), (bool(complete), feats))  # ON CONFLICT DO NOTHING

    engine = FeatureEngine(r, fcfg, context, writer, dedup_ttl_seconds=3600, dedup_prefix=DEDUP_PREFIX)
    stage = DedupStage(r, window_seconds=3600, bloom_capacity=200_000, bloom_fp_rate=0.01, key_prefix=DEDUP_PREFIX)
    return r, engine, stage


def run_stream(variant: str, records: List[dict], batch_size: int, redis_factory, fcfg, context, seed: int = 7) -> Tuple[Dict, Dict]:
    collected: Dict = {}
    r, engine, stage = make_process(redis_factory, fcfg, context, collected)
    clear_state(r, sorted({x["vehicle_id"] for x in records}))
    batches = [records[i: i + batch_size] for i in range(0, len(records), batch_size)]
    info = {"variant": variant, "batches": len(batches), "events": len(records), "redelivered": 0}

    if variant == "dup15":
        rng = random.Random(seed)
        delayed: Dict[int, List[dict]] = {}
        for bi, b in enumerate(batches):
            for rec in b:
                if rng.random() < 0.15:
                    delayed.setdefault(bi + rng.randint(0, 3), []).append(rec)
                    info["redelivered"] += 1
        batches = [b + delayed.get(bi, []) for bi, b in enumerate(batches)]
        extra = [rec for k, v in delayed.items() if k >= len(batches) for rec in v]
        if extra:
            batches.append(extra)

    crash_at = len(batches) // 2 if variant == "restart" else None
    counts = {"received": 0, "accepted": 0, "applied": 0, "duplicates_skipped": 0}
    bi = 0
    while bi < len(batches):
        b = batches[bi]
        if bi == crash_at:
            # Crash: dedup check, sink and Lua step ran, the snapshot step and commit did not.
            batch_seen, accepted, ids = set(), [], []
            for ev in b:
                ident = stage.get_identity(ev)
                if not stage.is_duplicate(ident, batch_seen):
                    batch_seen.add(ident)
                    accepted.append(ev)
                    ids.append(ident)
            engine.apply_records(accepted)
            info["crash_batch"] = bi
            info["crash_applied_events"] = len(accepted)
            # Diagnostics: every replayed event's dedup key must exist, and no key for a later event.
            info["replay_keys_present"] = sum(r.exists(engine.dedup_key(ev["vehicle_id"], ev["seq"])) for ev in accepted)
            nxt = batches[bi + 1] if bi + 1 < len(batches) else []
            info["next_batch_keys_present"] = sum(r.exists(engine.dedup_key(ev["vehicle_id"], ev["seq"])) for ev in nxt)
            # Restart: new process state; the uncommitted batch is redelivered.
            r, engine, stage = make_process(redis_factory, fcfg, context, collected)
            crash_at = None
            continue
        res = process_batch(b, stage, sink_write=lambda evs: None, feature_engine=engine)
        for k in counts:
            counts[k] += res.get(k, 0)
        bi += 1
    info.update(counts)
    info["duplicates_dropped_by_dedup_stage"] = stage.counters["duplicates_dropped"]
    if variant == "restart":
        info["restart_consistent"] = (info["replay_keys_present"] == info["crash_applied_events"]
                                      and info["next_batch_keys_present"] == 0
                                      and info["duplicates_dropped_by_dedup_stage"] == info["crash_applied_events"]
                                      and info["duplicates_skipped"] == 0)
    return collected, info


def compare(a: Dict, b: Dict, label: str, exact: bool = False) -> Dict:
    ka, kb = set(a), set(b)
    out = {"comparison": label, "snapshots_a": len(ka), "snapshots_b": len(kb),
           "missing_in_b": len(ka - kb), "extra_in_b": len(kb - ka), "values_compared": 0,
           "mismatches": 0, "window_complete_mismatches": 0, "max_rel_diff": 0.0, "examples": []}
    for key in sorted(ka & kb):
        ca, fa = a[key]
        cb, fb = b[key]
        if ca != cb:
            out["window_complete_mismatches"] += 1
        for name in feature_names(key[1]):
            x, y = fa.get(name, float("nan")), fb.get(name, float("nan"))
            out["values_compared"] += 1
            xn, yn = x is None or (isinstance(x, float) and math.isnan(x)), y is None or (isinstance(y, float) and math.isnan(y))
            if xn or yn:
                ok = xn and yn
                rel = 0.0
            elif exact or is_integer_feature(name):
                ok = x == y
                rel = 0.0 if ok else abs(x - y) / max(abs(x), abs(y), 1e-12)
            else:
                rel = abs(x - y) / max(abs(x), abs(y), 1e-12) if x != y else 0.0
                ok = rel <= REL_TOL or abs(x - y) <= 1e-12
            out["max_rel_diff"] = max(out["max_rel_diff"], rel)
            if not ok:
                out["mismatches"] += 1
                if len(out["examples"]) < 10:
                    out["examples"].append({"key": [key[0], key[1], key[2]], "feature": name, "a": x, "b": y})
    out["pass"] = (out["missing_in_b"] == 0 and out["extra_in_b"] == 0 and out["mismatches"] == 0
                   and out["window_complete_mismatches"] == 0 and out["snapshots_a"] > 0)
    return out


def offline_as_dict(df: pd.DataFrame) -> Dict:
    out = {}
    for rec in df.to_dict("records"):
        names = feature_names(rec["component"])
        out[(rec["vehicle_id"], rec["component"], int(rec["feature_ts_ms"]))] = (bool(rec["window_complete"]), {n: rec[n] for n in names})
    return out


def redis_memory_per_vehicle(r, vids) -> Dict:
    per = []
    for vid in vids:
        total = 0
        for k in r.scan_iter(match=f"{FS_PREFIX}:{vid}:*", count=1000):
            total += r.memory_usage(k) or 0
        per.append(total)
    return {"vehicles": len(per), "mean_bytes": int(sum(per) / max(1, len(per))), "max_bytes": int(max(per) if per else 0)}


def main():
    import psycopg2
    import redis
    import yaml

    p = argparse.ArgumentParser()
    p.add_argument("--sim-config", default="configs/offline_train.yaml")
    p.add_argument("--vehicles", type=int, default=20)
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--variants", default="clean,dup15,restart")
    p.add_argument("--timescale", default=os.getenv("TIMESCALE_DSN", "host=localhost port=5433 dbname=fleetpulse user=fleetpulse password=fleetpulse"))
    p.add_argument("--postgres", default=os.getenv("POSTGRES_DSN", "host=localhost port=5434 dbname=fleetpulse user=fleetpulse password=fleetpulse"))
    p.add_argument("--out-json", default="data/logs/feature_parity.json")
    args = p.parse_args()

    from simulator.config import load_config, SimulationRNG
    from simulator.population import FleetPopulation

    sim = load_config(args.sim_config)
    start = pd.Timestamp(sim.clock.start_time).to_pydatetime()
    end = start + timedelta(days=args.days)
    sim.population.vehicles = args.vehicles
    vids = [v.vehicle_id for v in FleetPopulation.generate_population(sim, SimulationRNG(sim.seed).population)]
    fcfg = replace(load_feature_config(), redis_key_prefix=FS_PREFIX)

    ts_conn, pg_conn = psycopg2.connect(args.timescale), psycopg2.connect(args.postgres)
    context = StaticContextProvider.load(pg_conn)

    t0 = time.time()
    offline = offline_as_dict(build_offline_features(ts_conn, pg_conn, vids, start, end, fcfg))
    t_off = time.time() - t0

    df = fetch_telemetry(ts_conn, vids, start, end).sort_values(["event_ts_ms", "vehicle_id", "seq"], kind="mergesort")
    records = list(records_from_frame(df))
    redis_factory = lambda: redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)

    report = {"vehicles": len(vids), "days": args.days, "period": [start.isoformat(), end.isoformat()],
              "telemetry_rows": len(records), "offline_snapshots": len(offline), "offline_seconds": round(t_off, 1),
              "runs": [], "comparisons": []}
    streams = {}
    for variant in args.variants.split(","):
        t1 = time.time()
        rows, info = run_stream(variant, records, args.batch_size, redis_factory, fcfg, context)
        info["seconds"] = round(time.time() - t1, 1)
        info["events_per_second"] = round(info["received"] / max(1e-9, info["seconds"]), 1)
        if variant == "clean":
            info["redis_memory"] = redis_memory_per_vehicle(redis_factory(), vids)
        streams[variant] = rows
        report["runs"].append(info)
        report["comparisons"].append(compare(offline, rows, f"offline vs streaming[{variant}]"))
    base = args.variants.split(",")[0]
    for variant in args.variants.split(",")[1:]:
        report["comparisons"].append(compare(streams[base], streams[variant], f"streaming[{base}] vs streaming[{variant}]", exact=True))
    report["pass"] = all(c["pass"] for c in report["comparisons"]) and all(r.get("restart_consistent", True) for r in report["runs"])

    os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
    with open(args.out_json, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Parity: {len(vids)} vehicles x {args.days} days, {len(records)} telemetry rows, {len(offline)} offline snapshots")
    for run in report["runs"]:
        print("  run", json.dumps({k: v for k, v in run.items() if k != "redis_memory"}))
        if "redis_memory" in run:
            print("  redis feature state per vehicle:", json.dumps(run["redis_memory"]))
    for c in report["comparisons"]:
        print(f"  {c['comparison']}: snapshots {c['snapshots_a']} vs {c['snapshots_b']}, missing {c['missing_in_b']}, extra {c['extra_in_b']}, "
              f"values {c['values_compared']}, mismatches {c['mismatches']}, window_complete mismatches {c['window_complete_mismatches']}, "
              f"max rel diff {c['max_rel_diff']:.3e} -> {'PASS' if c['pass'] else 'FAIL'}")
        for ex in c["examples"][:5]:
            print("     example:", ex)
    print("FEATURE PARITY:", "PASS" if report["pass"] else "FAIL")
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
