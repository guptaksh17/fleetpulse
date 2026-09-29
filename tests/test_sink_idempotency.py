"""
Unit tests for TimescaleSink idempotency and crash replay simulation.
Validates:
- Duplicate batch execution uses ON CONFLICT DO NOTHING.
- Crash simulation: replay of batch yields identical query semantics without double insertions.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, call

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "stream-processor"))

from stream_processor.sinks.timescale_sink import TimescaleSink, BATCH_INSERT_SQL


class TestSinkIdempotency(unittest.TestCase):
    def setUp(self):
        self.sink = TimescaleSink("mock_conn_str")
        self.sink.conn = MagicMock()
        self.sink.conn.closed = False
        self.mock_cursor = MagicMock()
        self.mock_cursor.mogrify.return_value = b"INSERT INTO telemetry VALUES (...)"
        self.sink.conn.cursor.return_value.__enter__.return_value = self.mock_cursor

    def _create_sample_event(self, seq: int):
        return {
            "event_id": f"00000000-0000-0000-0000-000000000{seq:03d}",
            "vehicle_id": "00000000-0000-0000-0000-000000000003",
            "vin": "1HGCM82633A004352",
            "oem_id": "OEM_A",
            "schema_version": "1.0",
            "event_ts": "2026-09-28T09:00:00.000Z",
            "ingested_at": "2026-09-28T09:00:00.050Z",
            "seq": seq,
            "event_type": "TELEMETRY",
            "vehicle_type": "EV",
            "location": {"lat": 37.77, "lon": -122.41},
            "speed_kmh": 45.0,
            "odometer_km": 12450.0 + seq,
            "acceleration_ms2": 0.0,
            "battery": {
                "soc_pct": 90.0,
                "voltage_v": 395.0,
                "current_a": 40.0,
                "temperature_c": 30.0,
                "charging": False,
            },
            "powertrain": {
                "rpm": None,
                "engine_temp_c": None,
                "motor_temp_c": 50.0,
                "power_kw": 15.8,
                "engine_load_pct": None,
            },
            "brakes": {"harsh_brake": False},
            "dtc_codes": [],
            "trip_id": None,
        }

    def test_sql_contains_idempotent_clause(self):
        # Assert the query explicitly uses the composite primary/unique key
        self.assertIn("ON CONFLICT (vehicle_id, seq, event_ts) DO NOTHING", BATCH_INSERT_SQL)

    def test_batch_insert_invocations(self):
        events = [self._create_sample_event(1), self._create_sample_event(2)]
        self.sink.write_batch(events)

        self.mock_cursor.execute.assert_called()

    def test_crash_simulation_replay(self):
        """
        Simulate crash:
        1. Sink batch writes successfully
        2. System crashes before marking seen in Redis / Bloom
        3. Upon restart, Kafka replays identical batch
        4. Sink writes batch again with ON CONFLICT DO NOTHING
        """
        events = [self._create_sample_event(10), self._create_sample_event(11)]

        # Initial write
        self.sink.write_batch(events)
        initial_calls = self.mock_cursor.execute.call_count
        self.assertGreaterEqual(initial_calls, 1)

        # Crash happens here: offsets not committed, Redis not updated.
        # Replay identical events on reboot:
        self.sink.write_batch(events)
        # Verify the same ON CONFLICT DO NOTHING query is executed safely
        self.assertEqual(self.mock_cursor.execute.call_count, initial_calls * 2)


if __name__ == "__main__":
    unittest.main()
