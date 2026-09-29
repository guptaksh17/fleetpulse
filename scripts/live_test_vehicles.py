#!/usr/bin/env python3
"""
Helper for live verification runs (smoke_live.sh, verify_phase4.sh).

  ids      print the population's vehicle ids and component ids as JSON
  reset    make a live run repeatable: delete the vehicles' telemetry and feature snapshots
           (optionally only from --after onwards), their maintenance events and trips from
           --after onwards, resolve their ACTIVE alerts, and delete their Redis dedup and
           feature-state keys
  resolve  resolve the vehicles' ACTIVE alerts (cleanup after a run, so the global alert
           counts checked by verify_phase2.sh are not affected)

Only the listed test vehicles are touched.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.config import load_config, SimulationRNG
from simulator.population import FleetPopulation

TS_DSN = os.getenv("TIMESCALE_DSN", "host=localhost port=5433 dbname=fleetpulse user=fleetpulse password=fleetpulse")
PG_DSN = os.getenv("POSTGRES_DSN", "host=localhost port=5434 dbname=fleetpulse user=fleetpulse password=fleetpulse")


def population(config_path: str, n: int):
    cfg = load_config(config_path)
    cfg.population.vehicles = n
    return FleetPopulation.generate_population(cfg, SimulationRNG(cfg.seed).population)


def resolve_alerts(pg, vids):
    with pg.cursor() as cur:
        cur.execute(
            """UPDATE alert SET status = 'RESOLVED'
               WHERE status = 'ACTIVE' AND vehicle_component_id IN
                 (SELECT vehicle_component_id FROM vehicle_component WHERE vehicle_id = ANY(%s::uuid[]))""",
            (vids,),
        )
        return cur.rowcount


def main():
    import psycopg2
    import redis

    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["ids", "reset", "resolve"])
    p.add_argument("--config", required=True)
    p.add_argument("--vehicles", type=int, required=True)
    p.add_argument("--after", default=None, help="Only delete data at or after this ISO timestamp")
    args = p.parse_args()

    vs = population(args.config, args.vehicles)
    vids = [v.vehicle_id for v in vs]
    if args.action == "ids":
        print(json.dumps([{"vehicle_id": v.vehicle_id, "vehicle_type": v.vehicle_type, "components": v.vehicle_component_ids} for v in vs]))
        return

    pg = psycopg2.connect(PG_DSN)
    pg.autocommit = True
    if args.action == "resolve":
        print(json.dumps({"alerts_resolved": resolve_alerts(pg, vids)}))
        return

    ts = psycopg2.connect(TS_DSN)
    ts.autocommit = True
    after = args.after or "-infinity"
    out = {}
    with ts.cursor() as cur:
        cur.execute("DELETE FROM telemetry WHERE vehicle_id = ANY(%s::uuid[]) AND event_ts >= %s", (vids, after))
        out["telemetry_deleted"] = cur.rowcount
        cur.execute("DELETE FROM component_features WHERE vehicle_id = ANY(%s::uuid[]) AND feature_ts >= %s", (vids, after))
        out["component_features_deleted"] = cur.rowcount
    with pg.cursor() as cur:
        cur.execute(
            """DELETE FROM maintenance_event WHERE occurred_at >= %s AND vehicle_component_id IN
                 (SELECT vehicle_component_id FROM vehicle_component WHERE vehicle_id = ANY(%s::uuid[]))""",
            (after, vids),
        )
        out["maintenance_events_deleted"] = cur.rowcount
        cur.execute("DELETE FROM trip WHERE vehicle_id = ANY(%s::uuid[]) AND started_at >= %s", (vids, after))
        out["trips_deleted"] = cur.rowcount
    out["alerts_resolved"] = resolve_alerts(pg, vids)

    r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")))
    deleted = 0
    for vid in vids:
        for pattern in (f"dedup:{vid}:*", f"fs:{vid}:*"):
            keys = list(r.scan_iter(match=pattern, count=1000))
            for i in range(0, len(keys), 1000):
                deleted += r.delete(*keys[i: i + 1000])
    out["redis_keys_deleted"] = deleted
    print(json.dumps(out))


if __name__ == "__main__":
    main()
