"""
TimescaleDB Idempotent Batch Sink for Stream Processor.
Inserts canonical telemetry records into TimescaleDB hypertable using ON CONFLICT DO NOTHING.
"""

import logging
import time
from typing import List

try:
    import psycopg2
    from psycopg2.extras import execute_batch
    DB_OPERATIONAL_ERROR = (psycopg2.OperationalError,)
except ImportError:
    psycopg2 = None
    execute_batch = None
    DB_OPERATIONAL_ERROR = ()

logger = logging.getLogger("stream-processor.sink")

BATCH_INSERT_SQL = """
INSERT INTO telemetry (
    event_ts,
    event_id,
    vehicle_id,
    seq,
    oem_id,
    vehicle_type,
    latitude,
    longitude,
    speed_kmh,
    odometer_km,
    soc_pct,
    voltage_v,
    current_a,
    temperature_c,
    motor_temp_c,
    engine_temp_c,
    power_kw,
    engine_load_pct,
    rpm,
    acceleration_ms2,
    harsh_brake,
    event_type,
    dtc_codes,
    ingested_at
) VALUES (
    %(event_ts)s,
    %(event_id)s,
    %(vehicle_id)s,
    %(seq)s,
    %(oem_id)s,
    %(vehicle_type)s,
    %(latitude)s,
    %(longitude)s,
    %(speed_kmh)s,
    %(odometer_km)s,
    %(soc_pct)s,
    %(voltage_v)s,
    %(current_a)s,
    %(temperature_c)s,
    %(motor_temp_c)s,
    %(engine_temp_c)s,
    %(power_kw)s,
    %(engine_load_pct)s,
    %(rpm)s,
    %(acceleration_ms2)s,
    %(harsh_brake)s,
    %(event_type)s,
    %(dtc_codes)s,
    %(ingested_at)s
)
ON CONFLICT (vehicle_id, seq, event_ts) DO NOTHING;
"""


class TimescaleSink:
    def __init__(self, db_conn_str: str):
        self.db_conn_str = db_conn_str
        self.conn = None

    def connect(self, max_retries: int = 30):
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed.")

        retries = 0
        while retries < max_retries:
            try:
                logger.info("Connecting TimescaleSink to TimescaleDB...")
                self.conn = psycopg2.connect(self.db_conn_str)
                self.conn.autocommit = True
                logger.info("TimescaleSink connected successfully.")
                return
            except Exception as e:
                retries += 1
                logger.warning(
                    "TimescaleDB not ready (%s). Retrying in 2s (%d/%d)...",
                    e,
                    retries,
                    max_retries,
                )
                time.sleep(2)
        raise TimeoutError("Could not connect to TimescaleDB after max retries")

    def _event_to_record(self, event: dict) -> dict:
        location = event.get("location", {})
        battery = event.get("battery", {})
        powertrain = event.get("powertrain", {})
        brakes = event.get("brakes", {})

        return {
            "event_ts": event["event_ts"],
            "event_id": event["event_id"],
            "vehicle_id": event["vehicle_id"],
            "seq": event["seq"],
            "oem_id": event["oem_id"],
            "vehicle_type": event["vehicle_type"],
            "latitude": location.get("lat"),
            "longitude": location.get("lon"),
            "speed_kmh": event.get("speed_kmh"),
            "odometer_km": event.get("odometer_km"),
            "soc_pct": battery.get("soc_pct"),
            "voltage_v": battery.get("voltage_v"),
            "current_a": battery.get("current_a"),
            "temperature_c": battery.get("temperature_c"),
            "motor_temp_c": powertrain.get("motor_temp_c"),
            "engine_temp_c": powertrain.get("engine_temp_c"),
            "power_kw": powertrain.get("power_kw"),
            "engine_load_pct": powertrain.get("engine_load_pct"),
            "rpm": powertrain.get("rpm"),
            "acceleration_ms2": event.get("acceleration_ms2"),
            "harsh_brake": brakes.get("harsh_brake", False),
            "event_type": event.get("event_type", "TELEMETRY"),
            "dtc_codes": event.get("dtc_codes", []),
            "ingested_at": event["ingested_at"],
        }

    def write_batch(self, events: List[dict]):
        if not events:
            return

        if not self.conn or self.conn.closed:
            self.connect()

        records = [self._event_to_record(e) for e in events]

        try:
            with self.conn.cursor() as cur:
                if execute_batch:
                    execute_batch(cur, BATCH_INSERT_SQL, records)
                else:
                    for record in records:
                        cur.execute(BATCH_INSERT_SQL, record)
        except DB_OPERATIONAL_ERROR as e:
            logger.warning("Database error during batch write (%s). Reconnecting...", e)
            self.connect()
            with self.conn.cursor() as cur:
                if execute_batch:
                    execute_batch(cur, BATCH_INSERT_SQL, records)
                else:
                    for record in records:
                        cur.execute(BATCH_INSERT_SQL, record)
