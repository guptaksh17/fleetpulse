"""
OEM-B v2.0 serializer: the same observable signals as OEM-A, in OEM-B's own nested layout and
US units. Used to demonstrate onboarding a second OEM without touching downstream services.
"""

from datetime import datetime, timezone
from typing import Any, Dict

KMH_PER_MPH = 1.609344
KM_PER_MILE = 1.609344
MS2_PER_G = 9.80665


def c_to_f(c):
    return None if c is None else round(c * 9.0 / 5.0 + 32.0, 2)


class OEMBEnvelopeSerializer:
    OEM_ID = "OEM_B"
    SCHEMA_VERSION = "2.0"

    @classmethod
    def to_payload(cls, p: Dict[str, Any]) -> Dict[str, Any]:
        ts = datetime.fromisoformat(p["ts"].replace("Z", "+00:00"))
        events = []
        if p.get("event") == "HARSH_BRAKE":
            events.append("HARSH_BRAKING")
        if p.get("event") == "DTC":
            events.append("DTC_RAISED")
        has_battery = p.get("charging") is not None  # EV / hybrid traction battery
        return {
            "vehicle": {"vin": p["vin"]},
            "timestamp_epoch_ms": int(round(ts.timestamp() * 1000)),
            "sequence_no": int(p["sequence"]),
            "position": {"latitude": p["lat"], "longitude": p["lng"]},
            "motion": {
                "speed_mph": round(p["vehicleSpeed"] / KMH_PER_MPH, 4),
                "odometer_miles": round(p["mileageKm"] / KM_PER_MILE, 4),
                "accel_g": round((p.get("longitudinalAccel") or 0.0) / MS2_PER_G, 5),
            },
            "energy": {
                "soc_percent": p.get("batteryLevel"),
                "pack_voltage": p.get("batteryVoltage"),
                "pack_current": p.get("batteryCurrent"),
                "pack_temp_f": c_to_f(p.get("batteryTemp")),
                "charging": p.get("charging"),
            } if has_battery or p.get("batteryVoltage") is not None else None,
            "engine": {"rpm": p.get("engineRpm"), "coolant_temp_f": c_to_f(p.get("engineTemp")), "load_percent": p.get("engineLoadPct")},
            "motor": {"temp_f": c_to_f(p.get("motorTemperature")), "power_kw": p.get("powerKw")},
            "diagnostics": {"dtcs": list(p.get("faultCodes") or [])},
            "events": events,
            "trip_id": p.get("tripRef"),
        }

    @classmethod
    def serialize(cls, payload: Dict[str, Any], oem_id: str = OEM_ID) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
        return {"received_at": now_iso, "oem_id": oem_id, "schema_version": cls.SCHEMA_VERSION, "payload": cls.to_payload(payload)}
