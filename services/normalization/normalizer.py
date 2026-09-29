#!/usr/bin/env python3
"""
FleetPulse Normalization Service (Phase 2)
Consumes resolved vehicle envelopes from Kafka topic: vehicle.raw
Adapts OEM-A telemetry into canonical vehicle events using stateless AdapterFactory.
Publishes canonical events to Kafka topic: vehicle.normalized
Manual offset commit after successful publish.
"""

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

try:
    from kafka import KafkaConsumer, KafkaProducer
except ImportError:
    KafkaConsumer = None
    KafkaProducer = None

try:
    import jsonschema
except ImportError:
    jsonschema = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("normalization")


class BaseOEMAdapter:
    """Base class for OEM telemetry adapters."""
    def normalize(self, envelope: dict) -> dict:
        raise NotImplementedError


class OEMAAdapterV1(BaseOEMAdapter):
    """
    Stateless adapter for OEM-A Version 1.0 telemetry (Phase 2).
    Pure function of a single payload without per-vehicle memory.
    Mappings:
      longitudinalAccel -> acceleration_ms2
      event == "HARSH_BRAKE" -> brakes.harsh_brake = true (canonical event_type = TELEMETRY)
      event == "DTC" -> canonical event_type = DTC, brakes.harsh_brake = false
      event == "TELEMETRY" -> canonical event_type = TELEMETRY, brakes.harsh_brake = false
      vehicleSpeed    -> speed_kmh
      mileageKm       -> odometer_km
      batteryLevel    -> battery.soc_pct
      batteryVoltage  -> battery.voltage_v
      batteryCurrent  -> battery.current_a
      batteryTemp     -> battery.temperature_c
      motorTemperature-> powertrain.motor_temp_c
      faultCodes      -> dtc_codes
      sequence        -> seq
      ts              -> event_ts
      lat/lng         -> location.lat/lon
    """

    def normalize(self, envelope: dict) -> dict:
        payload = envelope["payload"]
        vehicle_id = envelope["vehicle_id"]
        vin = payload["vin"]
        oem_id = envelope.get("oem_id", "OEM_A")
        schema_version = envelope.get("schema_version", "1.0")
        vehicle_type = envelope.get("vehicle_type", "EV")
        event_ts = payload["ts"]

        speed_kmh = float(payload["vehicleSpeed"])
        mileage_km = float(payload["mileageKm"])
        accel_ms2 = float(payload.get("longitudinalAccel", 0.0))

        event_raw = payload.get("event", "TELEMETRY")
        is_harsh_brake = (event_raw == "HARSH_BRAKE")
        canonical_event_type = "DTC" if event_raw == "DTC" else "TELEMETRY"

        v = payload.get("batteryVoltage")
        i = payload.get("batteryCurrent")
        if payload.get("powerKw") is not None:
            power_kw = float(payload["powerKw"])
        elif v is not None and i is not None:
            power_kw = round((v * i) / 1000.0, 2)
        else:
            power_kw = None

        engine_temp_c = float(payload["engineTemp"]) if payload.get("engineTemp") is not None else None
        engine_load_pct = float(payload["engineLoadPct"]) if payload.get("engineLoadPct") is not None else None
        
        # Charging status
        if payload.get("charging") is not None:
            charging_val = bool(payload["charging"])
        elif vehicle_type == "ICE" and payload.get("batteryLevel") is None:
            charging_val = None
        else:
            charging_val = False

        trip_id = str(payload["tripRef"]) if payload.get("tripRef") is not None else None

        ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"

        canonical_event = {
            "event_id": str(uuid.uuid4()),
            "vehicle_id": vehicle_id,
            "vin": vin,
            "oem_id": oem_id,
            "schema_version": schema_version,
            "event_ts": event_ts,
            "ingested_at": ingested_at,
            "seq": int(payload["sequence"]),
            "event_type": canonical_event_type,
            "vehicle_type": vehicle_type,
            "location": {
                "lat": float(payload["lat"]),
                "lon": float(payload["lng"]),
            },
            "speed_kmh": speed_kmh,
            "odometer_km": mileage_km,
            "acceleration_ms2": accel_ms2,
            "battery": {
                "soc_pct": float(payload["batteryLevel"]) if payload.get("batteryLevel") is not None else None,
                "voltage_v": float(payload["batteryVoltage"]) if payload.get("batteryVoltage") is not None else None,
                "current_a": float(payload["batteryCurrent"]) if payload.get("batteryCurrent") is not None else None,
                "temperature_c": float(payload["batteryTemp"]) if payload.get("batteryTemp") is not None else None,
                "charging": charging_val,
            },
            "powertrain": {
                "rpm": float(payload["engineRpm"]) if payload.get("engineRpm") is not None else None,
                "engine_temp_c": engine_temp_c,
                "motor_temp_c": float(payload["motorTemperature"]) if payload.get("motorTemperature") is not None else None,
                "power_kw": power_kw,
                "engine_load_pct": engine_load_pct,
            },
            "brakes": {
                "harsh_brake": bool(is_harsh_brake),
            },
            "dtc_codes": list(payload.get("faultCodes", [])),
            "trip_id": trip_id,
        }

        return canonical_event


class OEMBAdapterV2(BaseOEMAdapter):
    """
    OEM-B v2.0: nested payload in US units -> canonical event (same shape as OEM-A).
      vehicle.vin -> vin                       timestamp_epoch_ms -> event_ts (ISO 8601, UTC, ms)
      sequence_no -> seq                       position.latitude/longitude -> location.lat/lon
      motion.speed_mph -> speed_kmh (x 1.609344)            motion.odometer_miles -> odometer_km (x 1.609344)
      motion.accel_g -> acceleration_ms2 (x 9.80665)        *_temp_f -> *_c ((F - 32) x 5/9)
      events: HARSH_BRAKING -> brakes.harsh_brake; DTC_RAISED -> event_type DTC
    Pure function of its input (stateless), like every adapter.
    """

    KMH_PER_MPH = 1.609344
    KM_PER_MILE = 1.609344
    MS2_PER_G = 9.80665

    @staticmethod
    def _f_to_c(f):
        return None if f is None else round((float(f) - 32.0) * 5.0 / 9.0, 2)

    def normalize(self, envelope: dict) -> dict:
        p = envelope["payload"]
        motion = p.get("motion") or {}
        energy = p.get("energy") or {}
        engine = p.get("engine") or {}
        motor = p.get("motor") or {}
        events = set(p.get("events") or [])
        ts = datetime.fromtimestamp(int(p["timestamp_epoch_ms"]) / 1000.0, tz=timezone.utc)
        num = lambda v: None if v is None else float(v)
        return {
            "event_id": str(uuid.uuid4()),
            "vehicle_id": envelope["vehicle_id"],
            "vin": p["vehicle"]["vin"],
            "oem_id": envelope.get("oem_id", "OEM_B"),
            "schema_version": envelope.get("schema_version", "2.0"),
            "event_ts": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z",
            "seq": int(p["sequence_no"]),
            "event_type": "DTC" if "DTC_RAISED" in events else "TELEMETRY",
            "vehicle_type": envelope.get("vehicle_type", "EV"),
            "location": {"lat": float(p["position"]["latitude"]), "lon": float(p["position"]["longitude"])},
            "speed_kmh": round(float(motion["speed_mph"]) * self.KMH_PER_MPH, 2),
            "odometer_km": round(float(motion["odometer_miles"]) * self.KM_PER_MILE, 3),
            "acceleration_ms2": round(float(motion.get("accel_g") or 0.0) * self.MS2_PER_G, 2),
            "battery": {
                "soc_pct": num(energy.get("soc_percent")),
                "voltage_v": num(energy.get("pack_voltage")),
                "current_a": num(energy.get("pack_current")),
                "temperature_c": self._f_to_c(energy.get("pack_temp_f")),
                "charging": energy.get("charging"),
            },
            "powertrain": {
                "rpm": num(engine.get("rpm")),
                "engine_temp_c": self._f_to_c(engine.get("coolant_temp_f")),
                "motor_temp_c": self._f_to_c(motor.get("temp_f")),
                "power_kw": num(motor.get("power_kw")),
                "engine_load_pct": num(engine.get("load_percent")),
            },
            "brakes": {"harsh_brake": "HARSH_BRAKING" in events},
            "dtc_codes": list((p.get("diagnostics") or {}).get("dtcs") or []),
            "trip_id": p.get("trip_id"),
        }


class AdapterFactory:
    """
    Registry for OEM telemetry adapters.
    Phase 2 maintains registered (OEM_A, 1.0) stateless adapter.
    """
    def __init__(self):
        self._registry: Dict[Tuple[str, str], BaseOEMAdapter] = {
            ("OEM_A", "1.0"): OEMAAdapterV1(),
            ("OEM_B", "2.0"): OEMBAdapterV2(),  # onboarded in Phase 7: a new adapter, no downstream change
        }

    def get_adapter(self, oem_id: str, schema_version: str) -> Optional[BaseOEMAdapter]:
        return self._registry.get((oem_id, schema_version))


def load_canonical_schema(schema_path: str):
    if not jsonschema or not os.path.exists(schema_path):
        return None
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not load schema at %s: %s", schema_path, e)
        return None


def connect_kafka_consumer(bootstrap_servers: str, topic: str, group_id: str, max_retries: int = 30) -> KafkaConsumer:
    if KafkaConsumer is None:
        raise RuntimeError("kafka-python library is not installed.")

    retries = 0
    while retries < max_retries:
        try:
            logger.info("Connecting Kafka consumer to %s (topic: %s, group: %s)...", bootstrap_servers, topic, group_id)
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=bootstrap_servers.split(","),
                group_id=group_id,
                auto_offset_reset="earliest",
                enable_auto_commit=False,  # Disabled auto-commit per A3
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                key_deserializer=lambda k: k.decode("utf-8") if k else None,
            )
            logger.info("Kafka consumer connected successfully (auto-commit disabled).")
            return consumer
        except Exception as e:
            retries += 1
            logger.warning("Kafka consumer connect failed (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
            time.sleep(2)
    raise TimeoutError(f"Failed to connect Kafka consumer to {bootstrap_servers}")


def connect_kafka_producer(bootstrap_servers: str, max_retries: int = 30) -> KafkaProducer:
    if KafkaProducer is None:
        raise RuntimeError("kafka-python library is not installed.")

    retries = 0
    while retries < max_retries:
        try:
            logger.info("Connecting Kafka producer to %s...", bootstrap_servers)
            producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers.split(","),
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                retries=5,
                acks="all",
            )
            logger.info("Kafka producer connected successfully.")
            return producer
        except Exception as e:
            retries += 1
            logger.warning("Kafka producer connect failed (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
            time.sleep(2)
    raise TimeoutError(f"Failed to connect Kafka producer to {bootstrap_servers}")


def main():
    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    raw_topic = os.getenv("KAFKA_RAW_TOPIC", "vehicle.raw")
    normalized_topic = os.getenv("KAFKA_NORMALIZED_TOPIC", "vehicle.normalized")
    group_id = os.getenv("KAFKA_GROUP_ID", "normalization-group")
    schema_path = os.getenv("CANONICAL_SCHEMA_PATH", "/contracts/canonical/vehicle_event.schema.json")

    if raw_topic == normalized_topic:
        raise ValueError(f"Topology invariant violation: raw topic '{raw_topic}' must not equal normalized topic '{normalized_topic}'")

    logger.info("Initializing Normalization Service (Phase 2 - Stateless)...")
    logger.info("Topology: Consuming from '%s' -> Publishing to '%s'", raw_topic, normalized_topic)

    factory = AdapterFactory()
    canonical_schema = load_canonical_schema(schema_path)

    consumer = connect_kafka_consumer(bootstrap_servers, raw_topic, group_id)
    producer = connect_kafka_producer(bootstrap_servers)

    logger.info("Normalization Service ready. Consuming from %s -> publishing to %s", raw_topic, normalized_topic)

    try:
        for message in consumer:
            envelope = message.value
            if not isinstance(envelope, dict):
                logger.warning("Dropped invalid non-dict envelope: %s", envelope)
                consumer.commit()
                continue

            vehicle_id = envelope.get("vehicle_id")
            if not vehicle_id:
                # Still unresolved by identity-resolver; skip
                consumer.commit()
                continue

            oem_id = envelope.get("oem_id")
            schema_version = envelope.get("schema_version")

            if not oem_id or not schema_version:
                logger.error("Envelope missing oem_id or schema_version. Dropping: %s", envelope)
                consumer.commit()
                continue

            adapter = factory.get_adapter(oem_id, schema_version)
            if not adapter:
                logger.error("No adapter found for (%s, %s). Dropping.", oem_id, schema_version)
                consumer.commit()
                continue

            try:
                canonical_event = adapter.normalize(envelope)

                # Schema validation if schema is loaded
                if canonical_schema and jsonschema:
                    jsonschema.validate(instance=canonical_event, schema=canonical_schema)

                # Publish to vehicle.normalized with vehicle_id as partition key
                producer.send(normalized_topic, key=vehicle_id, value=canonical_event)
                producer.flush()

                # Commit offset only after downstream publish succeeds
                consumer.commit()

                logger.info(
                    "Normalized event seq=%d | vehicle_id=%s | speed=%.1f km/h | accel=%.2f m/s2 | harsh_brake=%s | type=%s",
                    canonical_event["seq"],
                    vehicle_id,
                    canonical_event["speed_kmh"],
                    canonical_event["acceleration_ms2"],
                    canonical_event["brakes"]["harsh_brake"],
                    canonical_event["event_type"],
                )
            except Exception as e:
                logger.error("Failed to normalize event (%s). Dropping: %s", e, envelope, exc_info=True)
                consumer.commit()
    except KeyboardInterrupt:
        logger.info("Normalization Service interrupted. Exiting.")
    finally:
        consumer.close()
        producer.close()
        logger.info("Normalization Service shutdown complete.")


if __name__ == "__main__":
    main()
