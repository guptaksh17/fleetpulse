"""
PostgreSQL Ground Truth Sink for FleetPulse Simulator.
Persists ground-truth maintenance events and trip records directly into PostgreSQL.
"""

from datetime import datetime
import json
import logging
import time
from typing import List, Optional

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    psycopg2 = None
    execute_values = None

from ..components import MaintenanceEventRecord
from ..trips import TripRecord

logger = logging.getLogger("simulator.sink.postgres")


class PostgresGroundTruthSink:
    def __init__(self, db_conn_str: str):
        self.db_conn_str = db_conn_str
        self.conn = None

    def connect(self, max_retries: int = 30):
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed.")

        retries = 0
        while retries < max_retries:
            try:
                logger.info("Connecting PostgresGroundTruthSink to PostgreSQL...")
                self.conn = psycopg2.connect(self.db_conn_str)
                self.conn.autocommit = True
                logger.info("PostgresGroundTruthSink connected successfully.")
                return
            except Exception as e:
                retries += 1
                logger.warning("Postgres not ready (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
                time.sleep(2)
        raise TimeoutError(f"Failed to connect PostgresGroundTruthSink")

    def save_maintenance_events(self, events: List[MaintenanceEventRecord]):
        if not events:
            return
        if not self.conn or self.conn.closed:
            self.connect()

        records = [
            (
                e.event_id,
                e.vehicle_component_id,
                e.event_type,
                e.occurred_at,
                e.odometer_km,
                json.dumps(e.metadata) if e.metadata else None,
            )
            for e in events
        ]

        query = """
        INSERT INTO maintenance_event (
            event_id,
            vehicle_component_id,
            event_type,
            occurred_at,
            odometer_km,
            metadata
        ) VALUES %s
        ON CONFLICT (event_id) DO NOTHING;
        """
        try:
            with self.conn.cursor() as cur:
                execute_values(cur, query, records)
        except Exception as e:
            logger.error("Failed to save maintenance events: %s", e)
            raise

    def save_trips(self, trips: List[TripRecord]):
        if not trips:
            return
        if not self.conn or self.conn.closed:
            self.connect()

        records = [
            (
                t.trip_id,
                t.vehicle_id,
                t.driver_id,
                t.started_at,
                t.ended_at,
                t.distance_km,
            )
            for t in trips
        ]

        query = """
        INSERT INTO trip (
            trip_id,
            vehicle_id,
            driver_id,
            started_at,
            ended_at,
            distance_km
        ) VALUES %s
        ON CONFLICT (trip_id) DO NOTHING;
        """
        try:
            with self.conn.cursor() as cur:
                execute_values(cur, query, records)
        except Exception as e:
            logger.error("Failed to save trip records: %s", e)
            raise

    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()
            logger.info("PostgresGroundTruthSink closed.")
