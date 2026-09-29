"""
TimescaleDB High-Speed Bulk Sink for Offline Simulation.
Inserts canonical telemetry records directly into TimescaleDB hypertable in high-throughput batches.
"""

from datetime import datetime, timezone
import logging
import time
from typing import List, Dict, Any
import uuid

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    psycopg2 = None
    execute_values = None

logger = logging.getLogger("simulator.sink.timescale")

TELEMETRY_EVENT_NAMESPACE = uuid.UUID("7e1e0a70-2f3b-4c1e-8d6a-74656c656d65")


class TimescaleBulkSink:
    def __init__(self, db_conn_str: str, batch_size: int = 2000):
        self.db_conn_str = db_conn_str
        self.batch_size = batch_size
        self.conn = None
        self.buffer: List[tuple] = []
        self.total_inserted: int = 0

    def connect(self, max_retries: int = 30):
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed.")

        retries = 0
        while retries < max_retries:
            try:
                logger.info("Connecting TimescaleBulkSink to TimescaleDB...")
                self.conn = psycopg2.connect(self.db_conn_str)
                self.conn.autocommit = True
                logger.info("TimescaleBulkSink connected successfully.")
                return
            except Exception as e:
                retries += 1
                logger.warning("TimescaleDB not ready (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
                time.sleep(2)
        raise TimeoutError("Failed to connect TimescaleBulkSink to TimescaleDB")

    def add_telemetry(self, vehicle, payload: Dict[str, Any]):
        """
        Transforms observable payload into canonical tuple and buffers.
        Strictly zero hidden fields. event_id is deterministic (vehicle_id, seq) so
        reruns are byte-identical; event_type follows the normalizer's rule.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        event_id = str(uuid.uuid5(TELEMETRY_EVENT_NAMESPACE, f"{vehicle.vehicle_id}:{payload['sequence']}"))

        is_harsh_brake = (payload.get("event") == "HARSH_BRAKE")
        dtc_codes = payload.get("faultCodes", [])
        event_type = "DTC" if payload.get("event") == "DTC" else "TELEMETRY"

        row = (
            payload["ts"],
            event_id,
            vehicle.vehicle_id,
            payload["sequence"],
            vehicle.oem_id,
            vehicle.vehicle_type,
            payload["lat"],
            payload["lng"],
            payload["vehicleSpeed"],
            payload["mileageKm"],
            payload.get("batteryLevel"),
            payload.get("batteryVoltage"),
            payload.get("batteryCurrent"),
            payload.get("batteryTemp"),
            payload.get("motorTemperature"),
            payload.get("engineTemp"),
            payload.get("powerKw"),
            payload.get("engineLoadPct"),
            payload.get("engineRpm"),
            payload.get("longitudinalAccel"),
            is_harsh_brake,
            event_type,
            dtc_codes,
            now_iso,
        )
        self.buffer.append(row)

        if len(self.buffer) >= self.batch_size:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        if not self.conn or self.conn.closed:
            self.connect()

        query = """
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
        ) VALUES %s
        ON CONFLICT (vehicle_id, seq, event_ts) DO NOTHING;
        """
        try:
            with self.conn.cursor() as cur:
                execute_values(cur, query, self.buffer, page_size=1000)
            self.total_inserted += len(self.buffer)
            self.buffer.clear()
        except Exception as e:
            logger.error("Failed to insert telemetry batch: %s", e)
            raise

    def close(self):
        self.flush()
        if self.conn and not self.conn.closed:
            self.conn.close()
            logger.info("TimescaleBulkSink closed (total inserted: %d rows).", self.total_inserted)
