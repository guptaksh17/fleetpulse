"""Small simulated telemetry fixtures (TimescaleDB telemetry column layout) for feature tests."""

from datetime import timedelta
import math

import pandas as pd

from simulator.config import load_config
from simulator.engine import FleetEngine, parse_utc

PAYLOAD_TO_COLUMN = {
    "vehicleSpeed": "speed_kmh", "mileageKm": "odometer_km", "longitudinalAccel": "acceleration_ms2",
    "batteryLevel": "soc_pct", "batteryVoltage": "voltage_v", "batteryCurrent": "current_a",
    "batteryTemp": "temperature_c", "motorTemperature": "motor_temp_c", "engineTemp": "engine_temp_c",
    "powerKw": "power_kw", "engineLoadPct": "engine_load_pct", "engineRpm": "rpm",
}


def payload_row(vehicle_id: str, p: dict) -> dict:
    row = {
        "vehicle_id": vehicle_id,
        "event_ts_ms": int(round(parse_utc(p["ts"]).timestamp() * 1000)),
        "seq": int(p["sequence"]),
        "harsh_brake": p["event"] == "HARSH_BRAKE",
        "dtc_codes": list(p["faultCodes"]),
    }
    for k, c in PAYLOAD_TO_COLUMN.items():
        v = p.get(k)
        row[c] = float("nan") if v is None else float(v)
    return row


def simulate_frame(vehicles: int = 3, days: float = 3.0, seed: int = 42):
    """Returns (telemetry frame, context dict, engine) for a short in-memory run."""
    cfg = load_config("configs/offline_train.yaml")
    cfg.population.vehicles = vehicles
    cfg.seed = seed
    eng = FleetEngine(cfg)
    rows, services = [], {}

    def on_step(t, outs):
        for o in outs:
            rows.append(payload_row(o.vehicle.vehicle_id, o.payload))
            for e in o.events:
                if e.event_type == "SERVICE_COMPLETED":
                    services.setdefault(o.vehicle.vehicle_id, {}).setdefault(e.component, []).append(
                        (int(round(parse_utc(e.occurred_at).timestamp() * 1000)), e.odometer_km))

    eng.run(eng.start_time + timedelta(days=days), on_step)
    ctx = {
        v.vehicle_id: {
            "vehicle_type": v.vehicle_type,
            "manufacture_date_ms": int(round((eng.start_time - timedelta(days=round(v.age_years * 365.25))).timestamp() * 1000)),
            "components": sorted(v.components),
            "services": services.get(v.vehicle_id, {}),
        }
        for v in eng.vehicles
    }
    return pd.DataFrame(rows), ctx, eng


def same_features(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
            continue
        if x != y:
            return False
    return True
