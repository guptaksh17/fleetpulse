#!/usr/bin/env python3
"""
FleetPulse Telemetry Simulator (Phase 2)
Simulates a single connected EV emitting OEM-A formatted telemetry at 1 Hz.
Publishes raw OEM envelopes to Kafka topic: oem.inbound
Supports DTC injection and duplicate generation.
"""

import json
import logging
import math
import os
import random
import sys
import time
from datetime import datetime, timezone

try:
    from kafka import KafkaProducer
except ImportError:
    KafkaProducer = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("simulator")


class VehicleSimulator:
    VIN = "1HGCM82633A004352"
    OEM_ID = "OEM_A"
    SCHEMA_VERSION = "1.0"
    VEHICLE_TYPE = "EV"

    def __init__(
        self,
        inject_dtc_code: str = "",
        inject_dtc_after_s: int = 60,
        inject_dtc_duration_s: int = 10,
        initial_seq: int = 0,
    ):
        self.seq = initial_seq
        self.start_epoch = time.time()
        self.odometer_km = 12450.0
        self.speed_kmh = 0.0
        self.target_speed_kmh = 50.0
        self.battery_soc = 92.5
        self.battery_temp = 29.0
        self.motor_temp = 48.0
        self.lat = 37.774929
        self.lng = -122.419416
        self.heading_rad = random.uniform(0, 2 * math.pi)

        self.inject_dtc_code = inject_dtc_code
        self.inject_dtc_after_s = inject_dtc_after_s
        self.inject_dtc_duration_s = inject_dtc_duration_s

    def next_step(self) -> dict:
        self.seq += 1
        prev_speed = self.speed_kmh
        is_harsh_brake = False

        # Occasional harsh brake (~1 in 50 events)
        if self.seq > 5 and random.randint(1, 50) == 50:
            is_harsh_brake = True
            drop = random.uniform(22.0, 32.0)
            self.speed_kmh = max(0.0, self.speed_kmh - drop)
            self.target_speed_kmh = random.choice([0.0, 30.0, 45.0])
        else:
            if random.random() < 0.15:
                self.target_speed_kmh = random.choice([0.0, 25.0, 45.0, 65.0, 85.0, 95.0])

            speed_diff = self.target_speed_kmh - self.speed_kmh
            if abs(speed_diff) < 1.0:
                self.speed_kmh = self.target_speed_kmh
            elif speed_diff > 0:
                accel = random.uniform(2.0, 6.0)
                self.speed_kmh = min(self.target_speed_kmh, self.speed_kmh + accel)
            else:
                decel = random.uniform(3.0, 7.0)
                self.speed_kmh = max(self.target_speed_kmh, self.speed_kmh - decel)

        if self.speed_kmh > 0 and not is_harsh_brake:
            self.speed_kmh = max(0.0, min(100.0, self.speed_kmh + random.uniform(-0.5, 0.5)))

        # Compute longitudinal acceleration in m/s^2: (delta km/h) / 3.6
        longitudinal_accel = round((self.speed_kmh - prev_speed) / 3.6, 2)

        # Distance incremented
        delta_dist_km = self.speed_kmh / 3600.0
        self.odometer_km += delta_dist_km

        # Plausible GPS movement
        if self.speed_kmh > 0:
            self.heading_rad += random.uniform(-0.05, 0.05)
            delta_deg = delta_dist_km * 0.009
            self.lat += delta_deg * math.cos(self.heading_rad)
            self.lng += delta_deg * math.sin(self.heading_rad)

        # Battery dynamics (EV)
        if is_harsh_brake:
            battery_current = -18.0
        elif self.speed_kmh > 0:
            power_factor = (self.speed_kmh / 100.0)
            battery_current = 15.0 + (power_factor * 85.0) + random.uniform(-2.0, 2.0)
            drain = 0.002 + (power_factor * 0.006)
            self.battery_soc = max(5.0, self.battery_soc - drain)
        else:
            battery_current = 2.5 + random.uniform(-0.3, 0.3)
            self.battery_soc = max(5.0, self.battery_soc - 0.0005)

        base_voltage = 350.0 + (self.battery_soc / 100.0) * 55.0
        battery_voltage = max(320.0, base_voltage - (battery_current * 0.05))

        target_bat_temp = 28.0 + (battery_current / 120.0) * 8.0
        self.battery_temp += (target_bat_temp - self.battery_temp) * 0.02

        target_mot_temp = 42.0 + (self.speed_kmh / 100.0) * 22.0
        self.motor_temp += (target_mot_temp - self.motor_temp) * 0.03

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"

        # Determine event type and DTC codes
        elapsed = time.time() - self.start_epoch
        dtc_active = (
            bool(self.inject_dtc_code)
            and elapsed >= self.inject_dtc_after_s
            and elapsed < (self.inject_dtc_after_s + self.inject_dtc_duration_s)
        )

        if dtc_active:
            event_name = "DTC"
            fault_codes = [self.inject_dtc_code]
        elif is_harsh_brake:
            event_name = "HARSH_BRAKE"
            fault_codes = []
        else:
            event_name = "TELEMETRY"
            fault_codes = []

        # OEM-A format payload (amended in Phase 2 with longitudinalAccel)
        oem_payload = {
            "vin": self.VIN,
            "ts": now_iso,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "vehicleSpeed": round(self.speed_kmh, 2),
            "mileageKm": round(self.odometer_km, 3),
            "batteryLevel": round(self.battery_soc, 2),
            "batteryVoltage": round(battery_voltage, 2),
            "batteryCurrent": round(battery_current, 2),
            "batteryTemp": round(self.battery_temp, 2),
            "engineRpm": None,
            "motorTemperature": round(self.motor_temp, 2),
            "faultCodes": fault_codes,
            "longitudinalAccel": longitudinal_accel,
            "event": event_name,
            "sequence": self.seq,
        }

        # Raw OEM Kafka envelope (WITHOUT vehicle_id - added by identity resolver)
        envelope = {
            "received_at": now_iso,
            "oem_id": self.OEM_ID,
            "schema_version": self.SCHEMA_VERSION,
            "payload": oem_payload,
        }

        return envelope


def connect_kafka_producer(bootstrap_servers: str, max_retries: int = 30) -> KafkaProducer:
    if KafkaProducer is None:
        raise RuntimeError("kafka-python library is not installed.")

    retries = 0
    while retries < max_retries:
        try:
            logger.info("Attempting connection to Kafka at %s...", bootstrap_servers)
            producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers.split(","),
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8"),
                retries=5,
                acks="all",
            )
            logger.info("Successfully connected to Kafka!")
            return producer
        except Exception as e:
            retries += 1
            logger.warning(
                "Kafka not ready (%s). Retrying in 2s (%d/%d)...",
                e,
                retries,
                max_retries,
            )
            time.sleep(2)

    raise TimeoutError(f"Failed to connect to Kafka at {bootstrap_servers} after {max_retries} attempts")


import argparse

try:
    from simulator.config import load_config
    from simulator.runner_live import run_live_simulation
except ImportError:
    try:
        from config import load_config
        from runner_live import run_live_simulation
    except ImportError:
        load_config = None
        run_live_simulation = None


def main():
    parser = argparse.ArgumentParser(description="FleetPulse Vehicle Simulator")
    parser.add_argument("--config", type=str, default=os.getenv("CONFIG_PATH", ""), help="Path to YAML config")
    parser.add_argument("--checkpoint-in", type=str, default=os.getenv("CHECKPOINT_IN", ""), help="Path to input checkpoint")
    parser.add_argument("--checkpoint-out", type=str, default=os.getenv("CHECKPOINT_OUT", ""), help="Path to output checkpoint")
    parser.add_argument("--speedup", type=float, default=None, help="Wall-clock speedup multiplier")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum simulation steps")
    parser.add_argument("--max-wall-seconds", type=float, default=float(os.getenv("MAX_WALL_SECONDS", "0")) or None)
    parser.add_argument("--vehicles", type=int, default=int(os.getenv("SIM_VEHICLES_OVERRIDE", "0")) or None)
    args, _ = parser.parse_known_args()

    config_file = args.config
    if config_file and os.path.exists(config_file) and load_config and run_live_simulation:
        cfg = load_config(config_file)
        if args.vehicles:
            cfg.population.vehicles = args.vehicles
        if not cfg.legacy:
            kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            pg_host = os.getenv("POSTGRES_HOST", "localhost")
            pg_port = os.getenv("POSTGRES_PORT", "5432")
            pg_db = os.getenv("POSTGRES_DB", "fleetpulse")
            pg_user = os.getenv("POSTGRES_USER", "fleetpulse")
            pg_pass = os.getenv("POSTGRES_PASSWORD", "fleetpulse")
            pg_conn_str = f"host={pg_host} port={pg_port} dbname={pg_db} user={pg_user} password={pg_pass}"

            run_live_simulation(
                config=cfg,
                kafka_bootstrap=kafka_bootstrap,
                postgres_conn_str=pg_conn_str,
                checkpoint_in=args.checkpoint_in or None,
                checkpoint_out=args.checkpoint_out or None,
                speedup_override=args.speedup,
                max_steps=args.max_steps,
                max_wall_seconds=args.max_wall_seconds,
                inject_dtc_code=os.getenv("INJECT_DTC_CODE") or None,
                duplicate_rate=float(os.getenv("DUPLICATE_RATE", "0.0")),
            )
            return

    # Fallback to single-vehicle legacy mode (Phase 1 & 2)
    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    topic = os.getenv("KAFKA_TOPIC", "oem.inbound")
    interval_sec = float(os.getenv("EMIT_INTERVAL_SEC", "1.0"))
    max_events = int(os.getenv("MAX_EVENTS", "0"))

    inject_dtc_code = os.getenv("INJECT_DTC_CODE", "")
    inject_dtc_after_s = int(os.getenv("INJECT_DTC_AFTER_S", "60"))
    inject_dtc_duration_s = int(os.getenv("INJECT_DTC_DURATION_S", "10"))
    duplicate_rate = float(os.getenv("DUPLICATE_RATE", "0.0"))
    start_seq = int(os.getenv("START_SEQ", "0"))

    logger.info(
        "Starting FleetPulse Simulator (Legacy / Single Vehicle) | Target: %s | Server: %s | Interval: %.1fs | StartSeq: %d | DTC: '%s' (after %ds, dur %ds) | DupRate: %.2f",
        topic,
        bootstrap_servers,
        interval_sec,
        start_seq,
        inject_dtc_code,
        inject_dtc_after_s,
        inject_dtc_duration_s,
        duplicate_rate,
    )

    producer = connect_kafka_producer(bootstrap_servers)
    simulator = VehicleSimulator(
        inject_dtc_code=inject_dtc_code,
        inject_dtc_after_s=inject_dtc_after_s,
        inject_dtc_duration_s=inject_dtc_duration_s,
        initial_seq=start_seq,
    )

    events_sent = 0
    try:
        while True:
            envelope = simulator.next_step()
            vin = envelope["payload"]["vin"]
            producer.send(topic, key=vin, value=envelope)
            producer.flush()
            events_sent += 1

            logger.info(
                "Emitted event #%d | VIN: %s | Speed: %.1f km/h | Accel: %.2f m/s2 | Odo: %.2f km | SoC: %.1f%% | Event: %s | DTC: %s",
                envelope["payload"]["sequence"],
                vin,
                envelope["payload"]["vehicleSpeed"],
                envelope["payload"]["longitudinalAccel"],
                envelope["payload"]["mileageKm"],
                envelope["payload"]["batteryLevel"],
                envelope["payload"]["event"],
                envelope["payload"]["faultCodes"],
            )

            # Duplicate injection if enabled
            if duplicate_rate > 0.0 and random.random() < duplicate_rate:
                producer.send(topic, key=vin, value=envelope)
                producer.flush()
                logger.warning(
                    "INJECTED DUPLICATE event #%d | VIN: %s | seq: %d",
                    envelope["payload"]["sequence"],
                    vin,
                    envelope["payload"]["sequence"],
                )

            if max_events > 0 and events_sent >= max_events:
                logger.info("Reached maximum events (%d). Stopping simulator.", max_events)
                break

            time.sleep(interval_sec)
    except KeyboardInterrupt:
        logger.info("Simulator interrupted by user. Shutting down.")
    finally:
        producer.close()
        logger.info("Simulator exited cleanly.")


if __name__ == "__main__":
    main()

