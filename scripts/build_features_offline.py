#!/usr/bin/env python3
"""
Offline reference feature builder (Phase 4 B5).

Computes the same hourly component snapshots as the streaming engine, directly from the
TimescaleDB telemetry table, using the shared statistic definition (fleetpulse_features.stats)
and the shared feature function (fleetpulse_features.features).

Because snapshots are hourly and every bucket width divides the hour, each window at a
snapshot time t is a union of whole hourly blocks (or the tail of one block):
  1h  window [t - 1h, t)   = hour block h = t/1h - 1
  5m  window [t - 5m, t)   = events of block h with minute-of-hour >= 55
  24h window [t - 24h, t)  = hour blocks h - 23 .. h, merged with a sliding window
Statistics are aggregated per (vehicle, hour block) with each statistic's merge operation,
then merged across blocks with the same operation. The result equals merging the 60 s and
900 s buckets used by the streaming engine.

Snapshot times per vehicle: every hour boundary t with first_event_ts < t <= last_event_ts.
Output: Parquet with one row per (vehicle, component, feature_ts) and one column per feature.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import sys
import time
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "stream-processor"))

from fleetpulse_features.config import FeatureConfig, load_feature_config
from fleetpulse_features.features import feature_names, features_from_window_stats, window_complete
from fleetpulse_features.stats import COUNT, SUM, MIN, MAX, FIRST, LAST, SIGNALS, STAT_SPEC, extract_stats
from stream_processor.features.context_cache import StaticContextProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
logger = logging.getLogger("build-features-offline")

HOUR_MS = 3_600_000

TELEMETRY_SQL = """
SELECT vehicle_id::text AS vehicle_id,
       (extract(epoch FROM event_ts) * 1000)::bigint AS event_ts_ms,
       seq, speed_kmh, odometer_km, acceleration_ms2, harsh_brake, dtc_codes, soc_pct,
       voltage_v, current_a, temperature_c, motor_temp_c, engine_temp_c, power_kw, engine_load_pct, rpm
FROM telemetry
WHERE vehicle_id = ANY(%(vids)s::uuid[]) AND event_ts >= %(start)s AND event_ts < %(end)s
ORDER BY vehicle_id, event_ts, seq
"""

FIRST_LAST = [s for s, op in STAT_SPEC.items() if op in (FIRST, LAST)]


def fetch_telemetry(ts_conn, vehicle_ids: List[str], start: datetime, end: datetime) -> pd.DataFrame:
    with ts_conn.cursor() as cur:
        cur.execute(TELEMETRY_SQL, {"vids": vehicle_ids, "start": start, "end": end})
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    for c in ("speed_kmh", "odometer_km", "acceleration_ms2", "soc_pct", *SIGNALS.values()):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(np.float64)
    return df


def event_stats_frame(df: pd.DataFrame, fcfg: FeatureConfig) -> pd.DataFrame:
    cols = {c: df[c].to_numpy() for c in df.columns if c != "dtc_codes"}
    cols["harsh_brake"] = df["harsh_brake"].fillna(False).to_numpy(dtype=bool)
    cols["dtc_codes"] = [c or [] for c in df["dtc_codes"]]
    stats = extract_stats(cols, fcfg.thresholds)
    out = pd.DataFrame(stats)
    out["vehicle_id"] = df["vehicle_id"].to_numpy()
    out["event_ts_ms"] = df["event_ts_ms"].to_numpy(dtype=np.int64)
    out["seq"] = df["seq"].to_numpy(dtype=np.int64)
    for s in FIRST_LAST:
        valid = out[s].notna()
        out[f"{s}__ts"] = np.where(valid, out["event_ts_ms"], np.nan)
        out[f"{s}__seq"] = np.where(valid, out["seq"], np.nan)
    return out


def aggregate_blocks(ev: pd.DataFrame, key: str) -> pd.DataFrame:
    """Per (vehicle, block) aggregation with each statistic's merge op. ev is time-ordered."""
    agg = {}
    for s, op in STAT_SPEC.items():
        if op in (COUNT, SUM):
            agg[s] = "sum"
        elif op == MIN:
            agg[s] = "min"
        elif op == MAX:
            agg[s] = "max"
        elif op == FIRST:
            agg[s] = "first"
            agg[f"{s}__ts"] = "first"
            agg[f"{s}__seq"] = "first"
        elif op == LAST:
            agg[s] = "last"
            agg[f"{s}__ts"] = "last"
            agg[f"{s}__seq"] = "last"
    return ev.groupby(["vehicle_id", key], sort=True).agg(agg)


def sliding_merge(block: pd.DataFrame, hours: np.ndarray, width: int) -> Dict[str, np.ndarray]:
    """
    block: per-hour stats for one vehicle indexed by hour index. Returns, for each hour index h
    in `hours`, the merge over blocks h - width + 1 .. h.
    """
    h0 = int(hours.min()) - width + 1
    h1 = int(hours.max())
    dense = block.reindex(np.arange(h0, h1 + 1))
    pos = hours - h0  # position of h in dense
    out = {}
    for s, op in STAT_SPEC.items():
        col = dense[s].to_numpy(dtype=np.float64)
        if op in (COUNT, SUM):
            col = np.nan_to_num(col, nan=0.0)
            win = np.lib.stride_tricks.sliding_window_view(col, width)  # row i covers dense[i .. i+width-1]
            out[s] = win.sum(axis=1)[pos - width + 1]
        elif op in (MIN, MAX):
            win = np.lib.stride_tricks.sliding_window_view(col, width)[pos - width + 1]
            with np.errstate(all="ignore"), _quiet():
                out[s] = np.nanmin(win, axis=1) if op == MIN else np.nanmax(win, axis=1)
        else:
            ts = dense[f"{s}__ts"].to_numpy(dtype=np.float64)
            sq = dense[f"{s}__seq"].to_numpy(dtype=np.float64)
            idx = np.arange(len(col), dtype=np.float64)
            src = pd.Series(np.where(np.isnan(col), np.nan, idx))
            if op == FIRST:
                pick = src.bfill().to_numpy()[pos - width + 1]
                ok = ~np.isnan(pick) & (pick <= pos)
            else:
                pick = src.ffill().to_numpy()[pos]
                ok = ~np.isnan(pick) & (pick >= pos - width + 1)
            p = np.where(ok, pick, 0).astype(np.int64)
            out[s] = np.where(ok, col[p], np.nan)
            out[f"{s}__ts"] = np.where(ok, ts[p], np.nan)
            out[f"{s}__seq"] = np.where(ok, sq[p], np.nan)
    return out


class _quiet:
    def __enter__(self):
        import warnings
        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore", category=RuntimeWarning)

    def __exit__(self, *a):
        self._w.__exit__(*a)


def vehicle_blocks(blocks: pd.DataFrame, vid: str) -> pd.DataFrame:
    if len(blocks) and vid in blocks.index.get_level_values(0):
        return blocks.loc[vid]
    return pd.DataFrame(columns=blocks.columns, dtype=np.float64)


def row_stats(arrays: Dict[str, np.ndarray], i: int) -> Dict[str, object]:
    """One merged-stats dict (same shape the streaming engine merges) from column arrays."""
    d = {}
    for s, op in STAT_SPEC.items():
        v = arrays[s][i]
        if op in (FIRST, LAST):
            if not np.isnan(v):
                d[s] = (int(arrays[f"{s}__ts"][i]), int(arrays[f"{s}__seq"][i]), float(v))
        elif op == COUNT:
            d[s] = int(v)
        elif op == SUM:
            d[s] = float(v)
        elif not np.isnan(v):
            d[s] = float(v)
    return d


def compute_features_frame(df: pd.DataFrame, context, fcfg: FeatureConfig, snapshot_times: Optional[Dict[str, List[int]]] = None) -> List[dict]:
    """
    Pure computation from a telemetry frame (TELEMETRY_SQL columns). By default a vehicle gets a
    snapshot at every hour boundary t with first_event_ts < t <= last_event_ts; snapshot_times
    (vehicle_id -> list of t in ms, multiples of one hour) overrides that.
    """
    if df.empty:
        return []
    ev = event_stats_frame(df, fcfg)
    ev["hour"] = ev["event_ts_ms"] // HOUR_MS
    minute_of_hour = (ev["event_ts_ms"] % HOUR_MS) // 60_000
    w5 = next(w for w in fcfg.windows if w.name == "5m")
    tail_minutes = 60 - w5.seconds // 60
    blocks_1h = aggregate_blocks(ev, "hour")
    blocks_5m = aggregate_blocks(ev[minute_of_hour >= tail_minutes], "hour")

    rows = []
    for vid, g in ev.groupby("vehicle_id", sort=True):
        ctx = context.get(vid)
        if not ctx:
            continue
        first_ts, last_ts = int(g["event_ts_ms"].min()), int(g["event_ts_ms"].max())
        if snapshot_times is not None:
            t_grid = np.array(sorted(snapshot_times.get(vid, [])), dtype=np.int64)
            if len(t_grid) == 0:
                continue
        else:
            t_first = (first_ts // HOUR_MS + 1) * HOUR_MS
            t_last = (last_ts // HOUR_MS) * HOUR_MS
            if t_last < t_first:
                continue
            t_grid = np.arange(t_first, t_last + 1, HOUR_MS, dtype=np.int64)
        h = t_grid // HOUR_MS - 1  # hour block just before t
        b1 = vehicle_blocks(blocks_1h, vid)
        win = {
            "1h": sliding_merge(b1, h, 1),
            "5m": sliding_merge(vehicle_blocks(blocks_5m, vid), h, 1),
            "24h": sliding_merge(b1, h, 24),
        }
        for i, t_ms in enumerate(t_grid):
            merged = {w: row_stats(arrs, i) for w, arrs in win.items()}
            for comp in ctx["components"]:
                svc = [s for s in ctx["services"].get(comp, []) if s[0] <= t_ms]
                static = {
                    "vehicle_type": ctx["vehicle_type"],
                    "feature_ts_ms": int(t_ms),
                    "first_event_ts_ms": first_ts,
                    "manufacture_date_ms": ctx.get("manufacture_date_ms"),
                    "last_service_ts_ms": svc[-1][0] if svc else None,
                    "last_service_odometer_km": svc[-1][1] if svc else None,
                }
                feats = features_from_window_stats(merged, static, comp)
                rows.append({
                    "vehicle_id": vid,
                    "component": comp,
                    "feature_ts_ms": int(t_ms),
                    "feature_schema_version": fcfg.schema_version,
                    "window_complete": window_complete(static),
                    **feats,
                })
    return rows


def build_for_vehicles(ts_conn, context, vehicle_ids: List[str], start: datetime, end: datetime, fcfg: FeatureConfig) -> List[dict]:
    return compute_features_frame(fetch_telemetry(ts_conn, vehicle_ids, start, end), context, fcfg)


ALL_FEATURES = sorted({n for c in ("BRAKE", "POWERTRAIN", "BATTERY") for n in feature_names(c)})
META_COLUMNS = ["vehicle_id", "component", "feature_ts_ms", "feature_schema_version", "window_complete"]


def rows_to_frame(rows: List[dict]) -> pd.DataFrame:
    """Fixed schema (every feature of every component, NaN where not applicable)."""
    df = pd.DataFrame(rows)
    for c in META_COLUMNS + ALL_FEATURES:
        if c not in df.columns:
            df[c] = np.nan
    df = df[META_COLUMNS + ALL_FEATURES]
    df[ALL_FEATURES] = df[ALL_FEATURES].astype(np.float64)
    df["feature_ts_ms"] = df["feature_ts_ms"].astype(np.int64)
    df["window_complete"] = df["window_complete"].astype(bool)
    return df


def list_vehicles(ts_conn, start: datetime, end: datetime) -> List[str]:
    with ts_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT vehicle_id::text FROM telemetry WHERE event_ts >= %s AND event_ts < %s ORDER BY 1", (start, end))
        return [r[0] for r in cur.fetchall()]


def iter_offline_chunks(ts_conn, pg_conn, vehicle_ids: Optional[List[str]], start: datetime, end: datetime,
                        fcfg: FeatureConfig, chunk: int = 25):
    """Yields one fixed-schema DataFrame per chunk of vehicles (bounded memory)."""
    context = StaticContextProvider.load(pg_conn)
    if vehicle_ids is None:
        vehicle_ids = list_vehicles(ts_conn, start, end)
    t0 = time.time()
    done = 0
    for k in range(0, len(vehicle_ids), chunk):
        part = vehicle_ids[k: k + chunk]
        rows = build_for_vehicles(ts_conn, context, part, start, end, fcfg)
        done += len(rows)
        logger.info("Vehicles %d/%d done, %d snapshot rows, %.0f s", min(k + chunk, len(vehicle_ids)), len(vehicle_ids), done, time.time() - t0)
        if rows:
            yield rows_to_frame(rows)


def build_offline_features(ts_conn, pg_conn, vehicle_ids: Optional[List[str]], start: datetime, end: datetime,
                           fcfg: FeatureConfig, chunk: int = 25) -> pd.DataFrame:
    """In-memory variant for small vehicle sets (parity, tests)."""
    parts = list(iter_offline_chunks(ts_conn, pg_conn, vehicle_ids, start, end, fcfg, chunk))
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=META_COLUMNS + ALL_FEATURES)
    out["feature_ts"] = pd.to_datetime(out["feature_ts_ms"], unit="ms", utc=True)
    return out


def main():
    import psycopg2
    import yaml

    p = argparse.ArgumentParser(description="FleetPulse offline reference feature builder")
    p.add_argument("--sim-config", default="configs/offline_train.yaml", help="Defines the default data period")
    p.add_argument("--start", default=None, help="ISO start (default: sim start_time)")
    p.add_argument("--end", default=None, help="ISO end, exclusive (default: start + sim days)")
    p.add_argument("--vehicles", default=None, help="Comma-separated vehicle ids (default: all with telemetry in range)")
    p.add_argument("--out", default="data/features/f1", help="Output directory of Parquet parts")
    p.add_argument("--write-db", action="store_true", help="Also insert rows into component_features")
    p.add_argument("--chunk", type=int, default=25)
    p.add_argument("--timescale", default=os.getenv("TIMESCALE_DSN", "host=localhost port=5433 dbname=fleetpulse user=fleetpulse password=fleetpulse"))
    p.add_argument("--postgres", default=os.getenv("POSTGRES_DSN", "host=localhost port=5434 dbname=fleetpulse user=fleetpulse password=fleetpulse"))
    args = p.parse_args()

    with open(args.sim_config) as f:
        sim = yaml.safe_load(f)
    start = datetime.fromisoformat((args.start or sim["clock"]["start_time"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(args.end.replace("Z", "+00:00")) if args.end else start + timedelta(days=int(sim["clock"]["days"]))
    fcfg = load_feature_config()
    vids = args.vehicles.split(",") if args.vehicles else None

    t0 = time.time()
    ts_conn, pg_conn = psycopg2.connect(args.timescale), psycopg2.connect(args.postgres)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.endswith(".parquet"):
            os.remove(os.path.join(out_dir, f))
    sink = None
    if args.write_db:
        sys.path.insert(0, os.path.join(ROOT, "services", "stream-processor"))
        from stream_processor.features.sink import ComponentFeatureSink
        sink = ComponentFeatureSink(lambda: psycopg2.connect(args.timescale))

    summary = {"rows": 0, "window_complete_rows": 0, "by_component": {}, "vehicles": 0, "parts": 0}
    for i, df in enumerate(iter_offline_chunks(ts_conn, pg_conn, vids, start, end, fcfg, chunk=args.chunk)):
        df.to_parquet(os.path.join(out_dir, f"part-{i:04d}.parquet"), index=False)
        summary["parts"] += 1
        summary["rows"] += int(len(df))
        summary["vehicles"] += int(df["vehicle_id"].nunique())
        summary["window_complete_rows"] += int(df["window_complete"].sum())
        for comp, n in df["component"].value_counts().items():
            summary["by_component"][comp] = summary["by_component"].get(comp, 0) + int(n)
        if sink is not None:
            batch = []
            for rec in df.to_dict("records"):
                batch.append((rec["vehicle_id"], rec["component"], rec["feature_ts_ms"], rec["feature_schema_version"],
                              rec["window_complete"], {k: rec[k] for k in feature_names(rec["component"])}))
                if len(batch) >= 5000:
                    sink.write(batch)
                    batch = []
            sink.write(batch)
    summary["period"] = [start.isoformat(), end.isoformat()]
    summary["runtime_seconds"] = round(time.time() - t0, 1)
    summary["out_dir"] = out_dir
    summary["written_to_db"] = bool(sink)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
