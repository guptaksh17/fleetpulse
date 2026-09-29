"""
Bucket statistics: the single shared definition used by the streaming engine and the
offline reference.

Input is a batch of flat telemetry records using the TimescaleDB telemetry column names
(event_ts_ms, seq, speed_kmh, odometer_km, acceleration_ms2, harsh_brake, dtc_codes, soc_pct,
voltage_v, current_a, temperature_c, motor_temp_c, engine_temp_c, power_kw, engine_load_pct, rpm).

extract_stats() maps every event to its contribution to each statistic. Every statistic is
mergeable with one of these operations:
  count  integer sum                      (a 0 contribution is a no-op)
  sum    float64 sum                      (a 0.0 contribution is a no-op)
  min    minimum, NaN means no contribution
  max    maximum, NaN means no contribution
  first  value of the earliest (event_ts_ms, seq) with a non-null value
  last   value of the latest (event_ts_ms, seq) with a non-null value

Signal values are quantized to float32 before use, because the telemetry hypertable stores
them as REAL. Live events from Kafka, events replayed from TimescaleDB and the offline
reference therefore see bit-identical inputs.

Nulls are skipped, never zero-filled.
"""

from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .dtc import COMPONENTS, dtc_component_map

COUNT, SUM, MIN, MAX, FIRST, LAST = "count", "sum", "min", "max", "first", "last"

# signal name -> telemetry column
SIGNALS: Dict[str, str] = {
    "battery_temp": "temperature_c",
    "engine_temp": "engine_temp_c",
    "motor_temp": "motor_temp_c",
    "voltage": "voltage_v",
    "current": "current_a",
    "power": "power_kw",
    "engine_load": "engine_load_pct",
    "rpm": "rpm",
}

# Every column the extractor may read. Anything else (in particular hidden simulator
# state) cannot influence a feature.
INPUT_COLUMNS: Tuple[str, ...] = (
    "event_ts_ms", "seq", "speed_kmh", "odometer_km", "acceleration_ms2", "harsh_brake",
    "dtc_codes", "soc_pct", *SIGNALS.values(),
)

# (signal, threshold key, stat name) for "events above threshold" counts
THRESHOLD_COUNTS: Tuple[Tuple[str, str, str], ...] = (
    ("engine_temp", "engine_temp_high_c", "engine_temp_high_count"),
    ("engine_load", "engine_load_high_pct", "engine_load_high_count"),
    ("motor_temp", "motor_temp_high_c", "motor_temp_high_count"),
    ("power", "power_high_kw", "power_high_count"),
    ("battery_temp", "battery_temp_high_c", "battery_temp_high_count"),
)


def _build_spec() -> Dict[str, str]:
    spec = {
        "n_events": COUNT,
        "n_moving": COUNT,
        "speed_sum": SUM,
        "speed_sumsq": SUM,
        "odo_min": MIN,
        "odo_max": MAX,
        "harsh_count": COUNT,
        "harsh_high_speed_count": COUNT,
        "harsh_accel_n": COUNT,
        "harsh_accel_sum": SUM,
        "accel_min": MIN,
        "brake_n": COUNT,
        "brake_accel_sum": SUM,
        "dtc_event_count": COUNT,
    }
    for c in COMPONENTS:
        spec[f"dtc_{c.lower()}_count"] = COUNT
    for s in SIGNALS:
        spec[f"{s}_n"] = COUNT
        spec[f"{s}_sum"] = SUM
        spec[f"{s}_sumsq"] = SUM
        spec[f"{s}_max"] = MAX
        spec[f"{s}_min"] = MIN
    for _, _, name in THRESHOLD_COUNTS:
        spec[name] = COUNT
    spec.update(
        {
            "current_absmax": MAX,
            "soc_min": MIN,
            "soc_max": MAX,
            "soc_first": FIRST,
            "soc_last": LAST,
            "deep_discharge_count": COUNT,
            "charging_count": COUNT,
            "fast_charge_count": COUNT,
        }
    )
    return spec


STAT_SPEC: Dict[str, str] = _build_spec()


def quantize(values) -> np.ndarray:
    """None -> NaN, then round-trip through float32 (TimescaleDB REAL storage precision)."""
    arr = np.array([np.nan if v is None else v for v in values], dtype=np.float64) if not isinstance(values, np.ndarray) else values.astype(np.float64)
    return arr.astype(np.float32).astype(np.float64)


def columns_from_records(records: Sequence[Mapping]) -> Dict[str, np.ndarray]:
    """Builds extractor input columns from flat telemetry dicts (streaming path)."""
    cols = {
        "event_ts_ms": np.array([int(r["event_ts_ms"]) for r in records], dtype=np.int64),
        "seq": np.array([int(r["seq"]) for r in records], dtype=np.int64),
        "harsh_brake": np.array([bool(r.get("harsh_brake")) for r in records], dtype=bool),
        "dtc_codes": [list(r.get("dtc_codes") or []) for r in records],
    }
    for c in ("speed_kmh", "odometer_km", "acceleration_ms2", "soc_pct", *SIGNALS.values()):
        cols[c] = np.array([np.nan if r.get(c) is None else float(r[c]) for r in records], dtype=np.float64)
    return cols


def extract_stats(cols: Mapping[str, object], thresholds: Mapping[str, float]) -> Dict[str, np.ndarray]:
    """
    Vectorized per-event contributions for every statistic in STAT_SPEC.
    cols: arrays of equal length (see INPUT_COLUMNS); dtc_codes is a sequence of lists.
    Returns stat name -> array (int64 for count, float64 otherwise).
    """
    n = len(cols["seq"])
    th = thresholds
    q = {c: quantize(np.asarray(cols[c], dtype=np.float64)) for c in ("speed_kmh", "odometer_km", "acceleration_ms2", "soc_pct", *SIGNALS.values())}
    nan = np.full(n, np.nan)
    out: Dict[str, np.ndarray] = {}

    def count(mask) -> np.ndarray:
        return np.asarray(mask, dtype=bool).astype(np.int64)

    def masked_sum(mask, values) -> np.ndarray:
        return np.where(mask, values, 0.0)

    def masked(mask, values) -> np.ndarray:
        return np.where(mask, values, nan)

    speed, accel, odo, soc = q["speed_kmh"], q["acceleration_ms2"], q["odometer_km"], q["soc_pct"]
    harsh = np.asarray(cols["harsh_brake"], dtype=bool)
    with np.errstate(invalid="ignore"):
        moving = speed > th["moving_speed_kmh"]
        out["n_events"] = np.ones(n, dtype=np.int64)
        out["n_moving"] = count(moving)
        out["speed_sum"] = masked_sum(moving, speed)
        out["speed_sumsq"] = masked_sum(moving, speed * speed)
        out["odo_min"] = odo.copy()
        out["odo_max"] = odo.copy()
        out["harsh_count"] = count(harsh)
        out["harsh_high_speed_count"] = count(harsh & (speed >= th["harsh_speed_kmh"]))
        harsh_accel = harsh & ~np.isnan(accel)
        out["harsh_accel_n"] = count(harsh_accel)
        out["harsh_accel_sum"] = masked_sum(harsh_accel, accel)
        out["accel_min"] = accel.copy()
        braking = accel < th["braking_accel_ms2"]
        out["brake_n"] = count(braking)
        out["brake_accel_sum"] = masked_sum(braking, accel)

        codes = cols["dtc_codes"]
        dmap = dtc_component_map()
        out["dtc_event_count"] = count([len(c) > 0 for c in codes])
        for comp in COMPONENTS:
            out[f"dtc_{comp.lower()}_count"] = count([any(dmap.get(x) == comp for x in c) for c in codes])

        for s, col in SIGNALS.items():
            v = q[col]
            present = ~np.isnan(v)
            out[f"{s}_n"] = count(present)
            out[f"{s}_sum"] = masked_sum(present, v)
            out[f"{s}_sumsq"] = masked_sum(present, v * v)
            out[f"{s}_max"] = v.copy()
            out[f"{s}_min"] = v.copy()
        for s, key, name in THRESHOLD_COUNTS:
            out[name] = count(q[SIGNALS[s]] > th[key])

        cur = q["current_a"]
        out["current_absmax"] = np.abs(cur)
        out["soc_min"] = soc.copy()
        out["soc_max"] = soc.copy()
        out["soc_first"] = soc.copy()
        out["soc_last"] = soc.copy()
        out["deep_discharge_count"] = count(soc < th["deep_discharge_soc_pct"])
        out["charging_count"] = count(cur < th["charging_current_a"])
        out["fast_charge_count"] = count(cur < th["fast_charge_current_a"])

    assert set(out) == set(STAT_SPEC), set(out) ^ set(STAT_SPEC)
    return out


# ----------------------------------------------------------------------------
# Scalar merging (streaming path and tests). A bucket is a dict stat -> value,
# where first/last values are (ts_ms, seq, value) tuples. Missing stat = no contribution.
# ----------------------------------------------------------------------------
def _key(t: Tuple[int, int, float]) -> Tuple[int, int]:
    return (t[0], t[1])


def merge_into(acc: Dict[str, object], stat: str, value) -> None:
    op = STAT_SPEC[stat]
    cur = acc.get(stat)
    if cur is None:
        acc[stat] = value
    elif op == COUNT or op == SUM:
        acc[stat] = cur + value
    elif op == MIN:
        acc[stat] = value if value < cur else cur
    elif op == MAX:
        acc[stat] = value if value > cur else cur
    elif op == FIRST:
        acc[stat] = value if _key(value) < _key(cur) else cur
    elif op == LAST:
        acc[stat] = value if _key(value) > _key(cur) else cur


def merge_buckets(buckets: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    acc: Dict[str, object] = {}
    for b in buckets:
        for stat, value in b.items():
            merge_into(acc, stat, value)
    return acc


def event_contributions(stats: Mapping[str, np.ndarray], i: int, ts_ms: int, seq: int) -> List[Tuple[str, str, object]]:
    """
    Non-trivial contributions of event i as (op, stat, value) triples; zero counts and sums
    and NaN min/max/first/last are dropped because they cannot change a merged result.
    first/last values become (ts_ms, seq, value).
    """
    ops = []
    for stat, op in STAT_SPEC.items():
        v = stats[stat][i]
        if op == COUNT:
            if v:
                ops.append((op, stat, int(v)))
        elif op == SUM:
            if v != 0.0:
                ops.append((op, stat, float(v)))
        elif not math.isnan(v):
            if op in (FIRST, LAST):
                ops.append((op, stat, (int(ts_ms), int(seq), float(v))))
            else:
                ops.append((op, stat, float(v)))
    return ops


def single_event_bucket(stats: Mapping[str, np.ndarray], i: int, ts_ms: int, seq: int) -> Dict[str, object]:
    return {stat: value for _, stat, value in event_contributions(stats, i, ts_ms, seq)}
