"""
Streaming feature engine (Phase 4).

State lives in Redis, per vehicle:
  {p}:{vid}:b{W}     hash, one per bucket width W; fields "{bucket_idx}:{stat}"
  {p}:{vid}:i{W}     zset of live bucket indices (used for pruning)
  {p}:{vid}:meta     hash: max_event_ts, first_event_ts, last_snapshot_ts (ms), events_applied
  {p}:{vid}:latest:{component}   JSON of the latest snapshot (read by Phase 5 inference)

apply_events() runs one Lua script per accepted event (pipelined). The script sets the
dedup key with NX and applies the bucket statistics in the same atomic step, so a replayed
event can never be counted twice.

snapshot_step() runs for every vehicle seen in a Kafka batch (duplicates included). For each
snapshot boundary t with last_snapshot_ts < t <= max_event_ts it merges the buckets in
[t - W, t), builds features with the shared definition, writes component_features rows
(ON CONFLICT DO NOTHING), caches the latest snapshot and only then advances
last_snapshot_ts. A crash between the Lua step and the snapshot write therefore loses
nothing: the next batch containing the vehicle (or the replay) produces the snapshot.
"""

from collections import defaultdict
import json
import logging
import math
import os
import time
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from fleetpulse_features.config import FeatureConfig
from fleetpulse_features.features import features_from_window_stats, window_complete
from fleetpulse_features.stats import (
    COUNT, SUM, MIN, MAX, FIRST, LAST, STAT_SPEC,
    columns_from_records, event_contributions, extract_stats, merge_buckets,
)

logger = logging.getLogger("stream-processor.features")

LUA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lua", "apply_event.lua")
OP_CODES = {COUNT: "c", SUM: "s", MIN: "mn", MAX: "mx", FIRST: "f", LAST: "l"}


def parse_ts_ms(value) -> int:
    """ISO-8601 string (Z or offset), datetime, or epoch ms -> epoch milliseconds."""
    from datetime import datetime, timezone

    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(round(dt.timestamp() * 1000))
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(round(dt.timestamp() * 1000))


def flatten_canonical_event(event: Mapping) -> Dict:
    """Canonical vehicle.normalized event -> flat record with telemetry column names."""
    battery = event.get("battery") or {}
    pt = event.get("powertrain") or {}
    brakes = event.get("brakes") or {}
    return {
        "vehicle_id": event["vehicle_id"],
        "vehicle_type": event.get("vehicle_type"),
        "seq": int(event["seq"]),
        "event_ts_ms": parse_ts_ms(event["event_ts"]),
        "speed_kmh": event.get("speed_kmh"),
        "odometer_km": event.get("odometer_km"),
        "acceleration_ms2": event.get("acceleration_ms2"),
        "harsh_brake": bool(brakes.get("harsh_brake", False)),
        "dtc_codes": list(event.get("dtc_codes") or []),
        "soc_pct": battery.get("soc_pct"),
        "voltage_v": battery.get("voltage_v"),
        "current_a": battery.get("current_a"),
        "temperature_c": battery.get("temperature_c"),
        "motor_temp_c": pt.get("motor_temp_c"),
        "engine_temp_c": pt.get("engine_temp_c"),
        "power_kw": pt.get("power_kw"),
        "engine_load_pct": pt.get("engine_load_pct"),
        "rpm": pt.get("rpm"),
    }


def _from_packed(stat: str, x):
    op = STAT_SPEC[stat]
    if op == COUNT:
        return int(float(x))
    if op in (FIRST, LAST):
        return (int(float(x[0])), int(float(x[1])), float(x[2]))
    return float(x)


class FeatureEngine:
    def __init__(
        self,
        redis_client,
        fcfg: FeatureConfig,
        context_provider,
        snapshot_writer: Callable[[List[Tuple]], None],
        dedup_ttl_seconds: int = 900,
        dedup_prefix: str = "dedup",
    ):
        """
        context_provider.get(vehicle_id) -> dict with vehicle_type, manufacture_date_ms,
            components (list of ACTIVE component names) and services
            (component -> sorted list of (ts_ms, odometer_km)); or None if unknown.
        snapshot_writer(rows) persists rows
            (vehicle_id, component, feature_ts_ms, schema_version, window_complete, features_json).
        """
        self.r = redis_client
        self.cfg = fcfg
        self.ctx = context_provider
        self.write_snapshots = snapshot_writer
        self.dedup_ttl = int(dedup_ttl_seconds)
        self.dedup_prefix = dedup_prefix
        self.snapshot_stream = f"{fcfg.redis_key_prefix}:snapshots"
        self.widths = fcfg.bucket_widths
        self.snap_ms = fcfg.snapshot_interval_seconds * 1000
        self.late_ms = fcfg.late_event_horizon_seconds * 1000
        with open(LUA_PATH, "r", encoding="utf-8") as f:
            self._lua = self.r.register_script(f.read())
        self.counters = defaultdict(float)
        self.snapshot_latencies_ms: List[float] = []

    # ------------------------------------------------------------------ keys
    def _k(self, vid: str, suffix: str) -> str:
        return f"{self.cfg.redis_key_prefix}:{vid}:{suffix}"

    def dedup_key(self, vid: str, seq: int) -> str:
        # Same key as DedupStage.get_redis_key(identity) with identity = "{vehicle_id}:{seq}"
        return f"{self.dedup_prefix}:{vid}:{seq}"

    # ------------------------------------------------------------------ apply
    def apply_records(self, records: Sequence[Mapping]) -> Dict[str, int]:
        """
        Applies flat telemetry records (already accepted by the dedup read check and
        written to the sink). Returns counts: applied, duplicates_skipped, late_ignored.
        """
        result = {"applied": 0, "duplicates_skipped": 0, "late_ignored": 0}
        if not records:
            return result
        vids = sorted({r["vehicle_id"] for r in records})
        pipe = self.r.pipeline(transaction=False)
        for vid in vids:
            pipe.hget(self._k(vid, "meta"), "max_event_ts")
        running_max = {vid: (int(float(m)) if m is not None else None) for vid, m in zip(vids, pipe.execute())}

        cols = columns_from_records(records)
        stats = extract_stats(cols, self.cfg.thresholds)

        pipe = self.r.pipeline(transaction=False)
        applied_idx = []
        late_keys = []
        for i, rec in enumerate(records):
            vid, ts, seq = rec["vehicle_id"], int(rec["event_ts_ms"]), int(rec["seq"])
            m = running_max[vid]
            if m is not None and ts < m - self.late_ms:
                # Older than the retention horizon: counted and ignored for features, but still
                # marked as seen so the Phase 2 dedup contract holds.
                late_keys.append(self.dedup_key(vid, seq))
                result["late_ignored"] += 1
                continue
            running_max[vid] = ts if m is None else max(m, ts)
            keys = [self.dedup_key(vid, seq), self._k(vid, "meta")]
            argv = [self.dedup_ttl, self.cfg.state_ttl_seconds, ts, seq, ts - ts % self.snap_ms, len(self.widths)]
            for w in self.widths:
                keys += [self._k(vid, f"b{w}"), self._k(vid, f"i{w}")]
                argv.append(ts // (w * 1000))
            ops = event_contributions(stats, i, ts, seq)
            argv.append(json.dumps({stat: [OP_CODES[op], value[2] if isinstance(value, tuple) else value]
                                    for op, stat, value in ops}, separators=(",", ":")))
            self._lua(keys=keys, args=argv, client=pipe)
            applied_idx.append(i)
        for k in late_keys:
            pipe.set(k, "1", ex=self.dedup_ttl, nx=True)
        res = pipe.execute()
        for flag in res[: len(applied_idx)]:
            if flag == 1:
                result["applied"] += 1
            else:
                result["duplicates_skipped"] += 1
        self.counters["events_applied"] += result["applied"]
        self.counters["duplicates_skipped"] += result["duplicates_skipped"]
        self.counters["late_events_ignored"] += result["late_ignored"]
        return result

    # ------------------------------------------------------------------ snapshots
    def _load_buckets(self, vid: str) -> Dict[int, Dict[int, Dict[str, object]]]:
        """Each hash field is one packed bucket: {stat: value}; first/last are [ts, seq, value]."""
        pipe = self.r.pipeline(transaction=False)
        for w in self.widths:
            pipe.hgetall(self._k(vid, f"b{w}"))
        out = {}
        for w, raw in zip(self.widths, pipe.execute()):
            buckets: Dict[int, Dict[str, object]] = {}
            for field, value in raw.items():
                f = field.decode() if isinstance(field, bytes) else field
                v = value.decode() if isinstance(value, bytes) else value
                buckets[int(f)] = {stat: _from_packed(stat, x) for stat, x in json.loads(v).items()}
            out[w] = buckets
        return out

    def _merged_windows(self, buckets, t_ms: int) -> Dict[str, Dict[str, object]]:
        merged = {}
        for win in self.cfg.windows:
            wb = buckets[win.bucket_seconds]
            end_idx = t_ms // (win.bucket_seconds * 1000)
            start_idx = end_idx - win.n_buckets
            merged[win.name] = merge_buckets(wb[i] for i in range(start_idx, end_idx) if i in wb)
        return merged

    def build_snapshot_rows(self, vid: str, buckets, t_ms: int, first_ts: int) -> List[Tuple]:
        ctx = self.ctx.get(vid)
        if not ctx:
            return []
        merged = self._merged_windows(buckets, t_ms)
        rows = []
        for comp in ctx["components"]:
            svc = [s for s in ctx["services"].get(comp, []) if s[0] <= t_ms]
            static = {
                "vehicle_type": ctx["vehicle_type"],
                "feature_ts_ms": t_ms,
                "first_event_ts_ms": first_ts,
                "manufacture_date_ms": ctx.get("manufacture_date_ms"),
                "last_service_ts_ms": svc[-1][0] if svc else None,
                "last_service_odometer_km": svc[-1][1] if svc else None,
            }
            feats = features_from_window_stats(merged, static, comp)
            rows.append((vid, comp, t_ms, self.cfg.schema_version, window_complete(static), feats))
        return rows

    def snapshot_step(self, vehicle_ids: Iterable[str]) -> int:
        """Emits every due snapshot for the given vehicles. Returns the number of rows written."""
        vids = sorted(set(vehicle_ids))
        if not vids:
            return 0
        t0 = time.time()
        pipe = self.r.pipeline(transaction=False)
        for vid in vids:
            pipe.hmget(self._k(vid, "meta"), "max_event_ts", "last_snapshot_ts", "first_event_ts")
        metas = pipe.execute()

        all_rows: List[Tuple] = []
        advance: Dict[str, int] = {}
        latest: Dict[str, Dict[str, Tuple]] = {}
        for vid, (mx, last, first) in zip(vids, metas):
            if mx is None or last is None:
                continue
            mx, last, first = int(float(mx)), int(float(last)), int(float(first))
            due = list(range(last + self.snap_ms, mx + 1, self.snap_ms))
            if not due:
                continue
            buckets = self._load_buckets(vid)
            for t_ms in due:
                rows = self.build_snapshot_rows(vid, buckets, t_ms, first)
                all_rows.extend(rows)
                for row in rows:
                    latest.setdefault(vid, {})[row[1]] = row
            advance[vid] = due[-1]

        if all_rows:
            self.write_snapshots(all_rows)
        pipe = self.r.pipeline(transaction=False)
        for vid, comps in latest.items():
            for comp, row in comps.items():
                pipe.set(
                    self._k(vid, f"latest:{comp}"),
                    json.dumps({"feature_ts_ms": row[2], "schema_version": row[3], "window_complete": row[4], "features": to_jsonable(row[5])}),
                    ex=self.cfg.state_ttl_seconds,
                )
                # Notify the risk scorer immediately (capped stream; the latest key is the payload).
                pipe.xadd(self.snapshot_stream, {"vehicle_id": vid, "component": comp, "feature_ts_ms": row[2]},
                          maxlen=200_000, approximate=True)
        for vid, t_ms in advance.items():
            pipe.hset(self._k(vid, "meta"), "last_snapshot_ts", t_ms)
        pipe.execute()
        for vid, t_ms in advance.items():
            self.prune(vid, t_ms)

        if advance:
            latency = (time.time() - t0) * 1000.0
            self.snapshot_latencies_ms.append(latency)
            self.snapshot_latencies_ms = self.snapshot_latencies_ms[-1000:]
        self.counters["snapshots_written"] += len(all_rows)
        return len(all_rows)

    def prune(self, vid: str, t_ms: int):
        """Drops buckets older than (t - retention) for each bucket width (one field per bucket)."""
        for w in self.widths:
            cutoff = (t_ms - self.cfg.retention_seconds(w) * 1000) // (w * 1000)
            zkey = self._k(vid, f"i{w}")
            old = self.r.zrangebyscore(zkey, "-inf", f"({cutoff}")
            if not old:
                continue
            pipe = self.r.pipeline(transaction=False)
            pipe.hdel(self._k(vid, f"b{w}"), *[str(int(float(i))) for i in old])
            pipe.zremrangebyscore(zkey, "-inf", f"({cutoff}")
            pipe.execute()

    def metrics(self) -> Dict[str, float]:
        lat = sorted(self.snapshot_latencies_ms)
        m = dict(self.counters)
        m["snapshot_latency_ms_p50"] = lat[len(lat) // 2] if lat else None
        m["snapshot_latency_ms_p95"] = lat[int(len(lat) * 0.95)] if lat else None
        return m


def to_jsonable(features: Mapping[str, float]) -> Dict[str, Optional[float]]:
    """NaN is not valid JSON: store it as null."""
    return {k: (None if (v is None or (isinstance(v, float) and math.isnan(v))) else v) for k, v in features.items()}


def from_jsonable(features: Mapping[str, Optional[float]]) -> Dict[str, float]:
    return {k: (float("nan") if v is None else float(v)) for k, v in features.items()}
