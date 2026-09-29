"""
Feature definitions (feature_schema_version f1).

features_from_window_stats(merged_stats, static_context, component) is the only place
feature formulas live. The streaming engine and the offline reference both call it.

merged_stats: {window name: {stat: value}} as produced by stats.merge_buckets (first/last
              values are (ts_ms, seq, value) tuples). A missing stat means no contribution.
static_context: vehicle_type, feature_ts_ms, manufacture_date_ms (or None),
                last_service_ts_ms (or None), last_service_odometer_km (or None),
                first_event_ts_ms.
Returns a flat dict of feature name -> float (NaN when undefined). Names are listed in
docs/feature-spec.md and by feature_names().
"""

import math
from typing import Dict, List, Mapping, Optional

NAN = float("nan")
WINDOW_NAMES = ("5m", "1h", "24h")
VEHICLE_TYPES = ("ICE", "EV", "HYBRID")
DAY_MS = 86_400_000.0

# Signals each vehicle type is expected to report. Features of a signal the vehicle type
# does not have are always NaN (absent), independent of the data.
SIGNAL_APPLICABILITY = {
    "engine_temp": {"ICE", "HYBRID"},
    "engine_load": {"ICE", "HYBRID"},
    "rpm": {"ICE", "HYBRID"},
    "motor_temp": {"EV", "HYBRID"},
    "power": {"EV", "HYBRID", "ICE"},
}


def _get(s: Mapping, k: str, default=0):
    v = s.get(k)
    return default if v is None else v


def _ratio(num: float, den: float) -> float:
    return num / den if den else NAN


def _std(sum_: float, sumsq: float, n: float) -> float:
    """Population std from running sums in float64, clamped at zero (see docs/feature-spec.md)."""
    if not n:
        return NAN
    mean = sum_ / n
    var = sumsq / n - mean * mean
    return math.sqrt(var) if var > 0.0 else 0.0


def _val(s: Mapping, k: str) -> float:
    v = s.get(k)
    if v is None:
        return NAN
    if isinstance(v, tuple):
        return float(v[2])
    return float(v)


def _signal_block(s: Mapping, sig: str, vehicle_type: str, stats: List[str], thr_stat: Optional[str] = None) -> Dict[str, float]:
    """mean/max/min/std (as requested) for one signal, NaN if absent for the type or window."""
    applicable = vehicle_type in SIGNAL_APPLICABILITY.get(sig, set(VEHICLE_TYPES))
    n = _get(s, f"{sig}_n")
    out = {}
    for st in stats:
        if not applicable or not n:
            out[st] = NAN
        elif st == "mean":
            out[st] = _get(s, f"{sig}_sum", 0.0) / n
        elif st == "std":
            out[st] = _std(_get(s, f"{sig}_sum", 0.0), _get(s, f"{sig}_sumsq", 0.0), n)
        elif st in ("max", "min"):
            out[st] = _val(s, f"{sig}_{st}")
    if thr_stat is not None:
        out["high"] = float(_get(s, thr_stat)) if (applicable and n) else NAN
    return out


def _common(s: Mapping, component: str) -> Dict[str, float]:
    n_moving = _get(s, "n_moving")
    odo_min, odo_max = _val(s, "odo_min"), _val(s, "odo_max")
    return {
        "events": float(_get(s, "n_events")),
        "moving_events": float(n_moving),
        "avg_speed_moving": _ratio(_get(s, "speed_sum", 0.0), n_moving),
        "speed_std_moving": _std(_get(s, "speed_sum", 0.0), _get(s, "speed_sumsq", 0.0), n_moving),
        "distance_km": (odo_max - odo_min) if not (math.isnan(odo_min) or math.isnan(odo_max)) else NAN,
        "dtc_events": float(_get(s, "dtc_event_count")),
        "component_dtc_events": float(_get(s, f"dtc_{component.lower()}_count")),
    }


def _brake(s: Mapping) -> Dict[str, float]:
    return {
        "harsh_brakes": float(_get(s, "harsh_count")),
        "harsh_high_speed": float(_get(s, "harsh_high_speed_count")),
        "min_accel": _val(s, "accel_min"),
        "mean_harsh_decel": -_ratio(_get(s, "harsh_accel_sum", 0.0), _get(s, "harsh_accel_n")),
        "mean_braking_decel": -_ratio(_get(s, "brake_accel_sum", 0.0), _get(s, "brake_n")),
    }


def _powertrain(s: Mapping, vt: str) -> Dict[str, float]:
    et = _signal_block(s, "engine_temp", vt, ["mean", "max", "std"], "engine_temp_high_count")
    el = _signal_block(s, "engine_load", vt, ["mean"], "engine_load_high_count")
    rpm = _signal_block(s, "rpm", vt, ["mean", "std"])
    mt = _signal_block(s, "motor_temp", vt, ["mean", "max", "std"], "motor_temp_high_count")
    # Power is reported by every type, but the high-power stress applies to electric drive only.
    pw = _signal_block(s, "power", vt, ["mean", "max"], "power_high_count")
    if vt == "ICE":
        pw = {k: NAN for k in pw}
    return {
        "engine_temp_mean": et["mean"], "engine_temp_max": et["max"], "engine_temp_std": et["std"],
        "high_engine_temp_events": et["high"],
        "engine_load_mean": el["mean"], "high_load_events": el["high"],
        "rpm_mean": rpm["mean"], "rpm_std": rpm["std"],
        "motor_temp_mean": mt["mean"], "motor_temp_max": mt["max"], "motor_temp_std": mt["std"],
        "high_motor_temp_events": mt["high"],
        "power_mean": pw["mean"], "power_max": pw["max"], "high_power_events": pw["high"],
    }


def _battery(s: Mapping, vt: str) -> Dict[str, float]:
    bt = _signal_block(s, "battery_temp", vt, ["mean", "max", "std"], "battery_temp_high_count")
    v = _signal_block(s, "voltage", vt, ["mean", "std", "min"])
    c = _signal_block(s, "current", vt, ["mean", "std"])
    soc_first, soc_last = _val(s, "soc_first"), _val(s, "soc_last")
    has_current = bool(_get(s, "current_n"))
    has_soc = not math.isnan(_val(s, "soc_min"))
    return {
        "soc_min": _val(s, "soc_min"),
        "soc_max": _val(s, "soc_max"),
        "soc_delta": soc_last - soc_first,
        "battery_temp_mean": bt["mean"], "battery_temp_max": bt["max"], "battery_temp_std": bt["std"],
        "high_temp_events": bt["high"],
        "voltage_mean": v["mean"], "voltage_std": v["std"], "voltage_min": v["min"],
        "current_mean": c["mean"], "current_std": c["std"],
        "current_max_abs": _val(s, "current_absmax") if has_current else NAN,
        "charging_events": float(_get(s, "charging_count")) if has_current else NAN,
        "fast_charge_events": float(_get(s, "fast_charge_count")) if has_current else NAN,
        "deep_discharge_events": float(_get(s, "deep_discharge_count")) if has_soc else NAN,
    }


def _static(ctx: Mapping) -> Dict[str, float]:
    t = ctx["feature_ts_ms"]
    svc_ts = ctx.get("last_service_ts_ms")
    svc_odo = ctx.get("last_service_odometer_km")
    odo = ctx.get("odometer_km", NAN)
    mfg = ctx.get("manufacture_date_ms")
    vt = ctx["vehicle_type"]
    out = {
        "days_since_service": (t - svc_ts) / DAY_MS if svc_ts is not None else NAN,
        "has_service_history": 1.0 if svc_ts is not None else 0.0,
        "distance_since_service_km": (odo - svc_odo) if (svc_ts is not None and svc_odo is not None and not math.isnan(odo)) else NAN,
        "vehicle_age_days": (t - mfg) / DAY_MS if mfg is not None else NAN,
        "odometer_km": odo,
    }
    for v in VEHICLE_TYPES:
        out[f"vt_{v.lower()}"] = 1.0 if vt == v else 0.0
    return out


def features_from_window_stats(merged_stats: Mapping[str, Mapping], static_context: Mapping, component: str) -> Dict[str, float]:
    vt = static_context["vehicle_type"]
    out: Dict[str, float] = {}
    for w in WINDOW_NAMES:
        s = merged_stats.get(w, {})
        block = _common(s, component)
        if component == "BRAKE":
            block.update(_brake(s))
        elif component == "POWERTRAIN":
            block.update(_powertrain(s, vt))
        elif component == "BATTERY":
            block.update(_battery(s, vt))
        else:
            raise ValueError(f"unknown component {component}")
        for k, v in block.items():
            out[f"w{w}_{k}"] = v
    ctx = dict(static_context)
    ctx["odometer_km"] = _val(merged_stats.get("24h", {}), "odo_max")
    out.update(_static(ctx))
    return out


def window_complete(static_context: Mapping) -> bool:
    return static_context["first_event_ts_ms"] <= static_context["feature_ts_ms"] - DAY_MS


def feature_names(component: str) -> List[str]:
    ctx = {"vehicle_type": "HYBRID", "feature_ts_ms": 0, "first_event_ts_ms": 0}
    return list(features_from_window_stats({}, ctx, component).keys())
