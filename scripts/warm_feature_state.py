#!/usr/bin/env python3
"""
Feature-state warm-up (Phase 4 B6).

Before a live run resumes from a simulator checkpoint, replay the last 25 h of telemetry per
vehicle from TimescaleDB through the streaming engine's apply path (the same Lua script:
dedup key SET NX plus bucket updates), so every window is complete for the first live
snapshot. Afterwards:
  first_event_ts   = the vehicle's first telemetry timestamp in TimescaleDB (full history)
  last_snapshot_ts = the last snapshot boundary at or before the last replayed event
so the engine does not recompute historical snapshots (the offline builder owns those) and
emits the next one as soon as live events cross the next boundary.

Existing feature state and dedup keys for the listed vehicles are deleted first.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "stream-processor"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from fleetpulse_features.config import load_feature_config
from stream_processor.features.engine import FeatureEngine
from build_features_offline import fetch_telemetry

HOUR_MS = 3_600_000


def records_from_frame(df):
    cols = list(df.columns)
    for row in df.itertuples(index=False, name=None):
        rec = dict(zip(cols, row))
        rec["event_ts_ms"] = int(rec["event_ts_ms"])
        rec["dtc_codes"] = rec["dtc_codes"] or []
        yield {k: (None if isinstance(v, float) and v != v else v) for k, v in rec.items()}


def warm(redis_client, ts_conn, vehicle_ids, until: datetime, fcfg, batch_size: int = 500, dedup_ttl: int = 900):
    # 1. Clear existing state for these vehicles.
    for vid in vehicle_ids:
        for pattern in (f"{fcfg.redis_key_prefix}:{vid}:*", f"dedup:{vid}:*"):
            keys = list(redis_client.scan_iter(match=pattern, count=1000))
            for i in range(0, len(keys), 1000):
                redis_client.delete(*keys[i: i + 1000])

    engine = FeatureEngine(redis_client, fcfg, context_provider=None, snapshot_writer=lambda rows: None, dedup_ttl_seconds=dedup_ttl)
    start = until - timedelta(seconds=fcfg.late_event_horizon_seconds)
    df = fetch_telemetry(ts_conn, list(vehicle_ids), start, until)
    df = df.sort_values(["event_ts_ms", "vehicle_id", "seq"], kind="mergesort")
    recs = list(records_from_frame(df))
    totals = {"applied": 0, "duplicates_skipped": 0, "late_ignored": 0}
    for i in range(0, len(recs), batch_size):
        res = engine.apply_records(recs[i: i + batch_size])
        for k in totals:
            totals[k] += res[k]

    # 2. Position the snapshot cursor and the history start.
    with ts_conn.cursor() as cur:
        cur.execute(
            "SELECT vehicle_id::text, (extract(epoch FROM min(event_ts)) * 1000)::bigint FROM telemetry "
            "WHERE vehicle_id = ANY(%s::uuid[]) AND event_ts < %s GROUP BY 1",
            (list(vehicle_ids), until),
        )
        first_ts = dict(cur.fetchall())
    last_ts = df.groupby("vehicle_id")["event_ts_ms"].max().to_dict() if len(df) else {}
    pipe = redis_client.pipeline(transaction=False)
    for vid, lt in last_ts.items():
        snap = int(lt) - int(lt) % (fcfg.snapshot_interval_seconds * 1000)
        pipe.hset(f"{fcfg.redis_key_prefix}:{vid}:meta", mapping={"first_event_ts": int(first_ts[vid]), "last_snapshot_ts": snap})
    pipe.execute()
    for vid, lt in last_ts.items():
        engine.prune(vid, int(lt) - int(lt) % (fcfg.snapshot_interval_seconds * 1000))
    totals["vehicles"] = len(last_ts)
    totals["replayed_rows"] = len(recs)
    totals["window_start"] = start.isoformat()
    totals["until"] = until.isoformat()
    return totals


def main():
    import psycopg2
    import redis

    sys.path.insert(0, ROOT)
    from simulator.config import load_config, SimulationRNG
    from simulator.population import FleetPopulation

    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/offline_train.yaml")
    p.add_argument("--vehicles", type=int, default=50)
    p.add_argument("--checkpoint", default="data/checkpoints/offline_checkpoint.json", help="Resume point (its sim_time)")
    p.add_argument("--until", default=None, help="ISO timestamp overriding the checkpoint sim_time")
    p.add_argument("--timescale", default=os.getenv("TIMESCALE_DSN", "host=localhost port=5433 dbname=fleetpulse user=fleetpulse password=fleetpulse"))
    p.add_argument("--dedup-ttl", type=int, default=int(os.getenv("DEDUP_WINDOW_SECONDS", "900")))
    args = p.parse_args()

    if args.until:
        until = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    else:
        with open(args.checkpoint) as f:
            until = datetime.fromisoformat(json.load(f)["sim_time"])
    cfg = load_config(args.config)
    cfg.population.vehicles = args.vehicles
    vids = [v.vehicle_id for v in FleetPopulation.generate_population(cfg, SimulationRNG(cfg.seed).population)]

    t0 = time.time()
    r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
    res = warm(r, psycopg2.connect(args.timescale), vids, until, load_feature_config(), dedup_ttl=args.dedup_ttl)
    res["runtime_seconds"] = round(time.time() - t0, 1)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
