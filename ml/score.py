#!/usr/bin/env python3
"""
Risk scoring (Phase 5 live inference and batch scoring).

  batch  score the latest offline snapshot of every vehicle component (data/features/f1)
  live   loop: read the latest streaming snapshot of each component from Redis
         (fs:{vehicle_id}:latest:{component}) and score it

Both modes upsert component_risk (calibrated P7d) and raise an ML alert
(source ML, alert_type ML_RISK_7D) when P7d >= the component threshold. The partial unique
index on alert keeps one ACTIVE alert per component and type; each new alert is written with
an ALERT_CREATED audit row in the same transaction.
"""

import argparse
import glob
import json
import logging
import math
import os
import sys
import time
import uuid

import joblib
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ml.model import COMPONENTS  # noqa: E402,F401  (needed to unpickle CalibratedModel)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [ml.score] %(message)s")
log = logging.getLogger("ml.score")
PG_DSN = os.getenv("POSTGRES_DSN", "host=localhost port=5434 dbname=fleetpulse user=fleetpulse password=fleetpulse")


def load_models(model_dir: str):
    return {c: joblib.load(os.path.join(model_dir, f"{c}.joblib")) for c in ("BRAKE", "POWERTRAIN", "BATTERY")}


def write_scores(conn, rows, model_version: str, refresh: bool = True):
    """rows: list of (vehicle_component_id, component, feature_ts, p7d, threshold, kind)."""
    from psycopg2.extras import execute_values

    if not rows:
        return 0
    created = 0
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO component_risk (vehicle_component_id, model_version, model_kind, feature_ts, p7d, threshold, scored_at)
            VALUES %s
            ON CONFLICT (vehicle_component_id) DO UPDATE SET
              model_version = EXCLUDED.model_version, model_kind = EXCLUDED.model_kind,
              feature_ts = EXCLUDED.feature_ts, p7d = EXCLUDED.p7d, threshold = EXCLUDED.threshold, scored_at = now()
            WHERE component_risk.feature_ts <= EXCLUDED.feature_ts""",
            [(r[0], model_version, r[5], r[2], r[3], r[4]) for r in rows],
            template="(%s, %s, %s, %s, %s, %s, now())", page_size=1000)
        for vcid, comp, fts, p, thr, kind in rows:
            if p < thr:
                # Risk dropped below the threshold: resolve the component's active ML alert (audited).
                cur.execute("""
                    UPDATE alert SET status = 'RESOLVED'
                    WHERE vehicle_component_id = %s AND alert_type = 'ML_RISK_7D' AND status = 'ACTIVE'
                    RETURNING alert_id::text""", (vcid,))
                for (rid,) in cur.fetchall():
                    cur.execute("""
                        INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, entity_type, entity_id, metadata)
                        SELECT f.tenant_id, 'SERVICE', 'ml-scorer', 'ALERT_AUTO_RESOLVED', 'alert', %s, %s
                        FROM vehicle_component vc JOIN vehicle v ON v.vehicle_id = vc.vehicle_id JOIN fleet f ON f.fleet_id = v.fleet_id
                        WHERE vc.vehicle_component_id = %s""", (rid, json.dumps({"p7d": float(p), "threshold": float(thr)}), vcid))
                continue
            sev = "CRITICAL" if p >= max(0.6, 2 * thr) else "HIGH"
            alert_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO alert (alert_id, vehicle_component_id, alert_type, severity, source, risk_probability, message, model_name, model_version)
                VALUES (%s, %s, 'ML_RISK_7D', %s, 'ML', %s, %s, %s, %s)
                ON CONFLICT (vehicle_component_id, alert_type) WHERE status = 'ACTIVE' DO NOTHING
                RETURNING alert_id""",
                (alert_id, vcid, sev, float(p), f"{comp}: {p:.0%} probability of maintenance or failure within 7 days",
                 f"{comp.lower()}_{kind}", model_version))
            if cur.fetchone():
                created += 1
                cur.execute("""
                    INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, entity_type, entity_id, metadata)
                    SELECT f.tenant_id, 'SERVICE', 'ml-scorer', 'ALERT_CREATED', 'alert', %s, %s
                    FROM vehicle_component vc JOIN vehicle v ON v.vehicle_id = vc.vehicle_id JOIN fleet f ON f.fleet_id = v.fleet_id
                    WHERE vc.vehicle_component_id = %s""",
                    (alert_id, json.dumps({"p7d": float(p), "threshold": float(thr), "model_version": model_version}), vcid))
    conn.commit()
    if refresh:
        refresh_priority(conn)
    return created


def refresh_priority(conn):
    with conn.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY maintenance_priority_mv")
    conn.commit()


def component_ids(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT vehicle_id::text, component, vehicle_component_id::text FROM vehicle_component WHERE status = 'ACTIVE'")
        return {(v, c): i for v, c, i in cur.fetchall()}


def batch(models, model_dir, features_dir, conn):
    """Scores the latest snapshot per component. Parts are processed one at a time (bounded memory);
    a vehicle never spans two parts because the offline builder chunks by vehicle."""
    ids = component_ids(conn)
    parts = sorted(glob.glob(os.path.join(features_dir, "*.parquet")))
    total_rows, total_created = 0, 0
    for part in parts:
        df = pd.read_parquet(part)
        df = df.sort_values("feature_ts_ms").groupby(["vehicle_id", "component"], as_index=False).tail(1)
        rows = []
        for comp, m in models.items():
            sub = df[df["component"] == comp]
            if sub.empty:
                continue
            X = sub.rename(columns={c: f"f_{c}" for c in sub.columns if not c.startswith("f_")})
            p = m.predict_proba(X)
            for vid, ts_ms, prob in zip(sub["vehicle_id"], sub["feature_ts_ms"], p):
                vcid = ids.get((vid, comp))
                if vcid:
                    rows.append((vcid, comp, pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").to_pydatetime(), float(prob), float(m.threshold), m.kind))
        total_created += write_scores(conn, rows, os.path.basename(model_dir.rstrip("/")), refresh=False)
        total_rows += len(rows)
    refresh_priority(conn)
    log.info("Batch scored %d components from %d parts, %d new ML alerts", total_rows, len(parts), total_created)
    return total_rows, total_created


def live(models, model_dir, conn, interval: float, prefix: str = "fs", refresh_seconds: float = 10.0):
    """
    Event-driven scoring: blocks on the stream processor's snapshot stream ({prefix}:snapshots),
    scores each new snapshot as soon as it is written, writes risk and ML alerts immediately, and
    refreshes the priority read model at most every refresh_seconds (a full refresh takes seconds
    at 250K components). Starts from new entries only ('$').
    """
    import redis

    r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
    stream = f"{prefix}:snapshots"
    last_id = "$"
    ids = component_ids(conn)
    ids_loaded = time.time()
    last_refresh = 0.0
    pending_refresh = False
    while True:
        resp = r.xread({stream: last_id}, count=1000, block=int(interval * 1000))
        if time.time() - ids_loaded > 60:
            ids, ids_loaded = component_ids(conn), time.time()
        if resp:
            t0 = time.time()
            entries = resp[0][1]
            last_id = entries[-1][0]
            wanted = {(e["vehicle_id"], e["component"]) for _, e in entries}
            keys = [f"{prefix}:{vid}:latest:{comp}" for vid, comp in wanted]
            rows = []
            for (vid, comp), v in zip(wanted, r.mget(keys)):
                if not v or comp not in models:
                    continue
                snap = json.loads(v)
                m = models[comp]
                X = pd.DataFrame([{f"f_{n}": (np.nan if x is None else x) for n, x in snap["features"].items()}])
                for f in m.features:
                    if f not in X.columns:
                        X[f] = np.nan
                p = float(m.predict_proba(X)[0])
                vcid = ids.get((vid, comp))
                if vcid:
                    rows.append((vcid, comp, pd.Timestamp(snap["feature_ts_ms"], unit="ms", tz="UTC").to_pydatetime(), p, float(m.threshold), m.kind))
            created = write_scores(conn, rows, os.path.basename(model_dir.rstrip("/")), refresh=False)
            pending_refresh = pending_refresh or bool(rows)
            log.info("Live scored %d snapshots, %d new ML alerts, %.0f ms", len(rows), created, 1000 * (time.time() - t0))
        if pending_refresh and time.time() - last_refresh >= refresh_seconds:
            refresh_priority(conn)
            last_refresh, pending_refresh = time.time(), False


def main():
    import psycopg2

    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["batch", "live"])
    ap.add_argument("--models", default=os.getenv("MODEL_DIR", "data/models/m1"))
    ap.add_argument("--features", default="data/features/f1")
    ap.add_argument("--interval", type=float, default=1.0, help="live: max block time on the snapshot stream (s)")
    args = ap.parse_args()
    models = load_models(args.models)
    conn = psycopg2.connect(PG_DSN)
    if args.mode == "batch":
        batch(models, args.models, args.features, conn)
    else:
        live(models, args.models, conn, args.interval)


if __name__ == "__main__":
    main()
