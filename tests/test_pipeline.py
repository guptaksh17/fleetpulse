#!/usr/bin/env python3
"""
Pipeline and Component Unit Tests for FleetPulse Phase 2.
Tests simulator physics progression, identity resolution, and sink mapping.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "simulator"))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "normalization"))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "identity-resolver"))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "stream-processor"))

from vehicle_simulator import VehicleSimulator
from resolver import IdentityResolver
from stream_processor.sinks.timescale_sink import TimescaleSink, BATCH_INSERT_SQL


class TestPipelineComponents(unittest.TestCase):
    def test_simulator_progression(self):
        sim = VehicleSimulator()
        steps = [sim.next_step() for _ in range(20)]

        # Check sequence numbers are strictly monotonic
        seqs = [s["payload"]["sequence"] for s in steps]
        self.assertEqual(seqs, list(range(1, 21)))

        # Check odometer is strictly non-decreasing
        odos = [s["payload"]["mileageKm"] for s in steps]
        for i in range(1, len(odos)):
            self.assertGreaterEqual(odos[i], odos[i - 1])

        # Check battery SoC is strictly bounded [0, 100]
        socs = [s["payload"]["batteryLevel"] for s in steps]
        for soc in socs:
            self.assertGreaterEqual(soc, 0.0)
            self.assertLessEqual(soc, 100.0)

        # Check speeds are non-negative
        speeds = [s["payload"]["vehicleSpeed"] for s in steps]
        for speed in speeds:
            self.assertGreaterEqual(speed, 0.0)
            self.assertLessEqual(speed, 110.0)

        # Check longitudinal acceleration exists and is float
        accels = [s["payload"]["longitudinalAccel"] for s in steps]
        for accel in accels:
            self.assertIsInstance(accel, (int, float))

    def test_identity_resolver_with_mock_db(self):
        resolver = IdentityResolver("mock_conn_str")
        resolver.pg_conn = MagicMock()
        resolver.pg_conn.closed = False
        mock_cursor = MagicMock()
        resolver.pg_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Simulate finding known VIN
        mock_cursor.fetchone.return_value = {
            "vehicle_id": "00000000-0000-0000-0000-000000000003",
            "vehicle_type": "EV",
            "oem_id": "OEM_A",
        }

        resolved = resolver.resolve_vin("1HGCM82633A004352")
        self.assertEqual(resolved["vehicle_id"], "00000000-0000-0000-0000-000000000003")
        self.assertEqual(resolved["vehicle_type"], "EV")

        # Second lookup should hit cache without querying cursor again
        mock_cursor.execute.reset_mock()
        cached = resolver.resolve_vin("1HGCM82633A004352")
        self.assertEqual(cached["vehicle_id"], "00000000-0000-0000-0000-000000000003")
        mock_cursor.execute.assert_not_called()

    def test_identity_resolver_fails_loudly_on_unknown_vin(self):
        resolver = IdentityResolver("mock_conn_str")
        resolver.pg_conn = MagicMock()
        resolver.pg_conn.closed = False
        mock_cursor = MagicMock()
        resolver.pg_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Return None (VIN not found)
        mock_cursor.fetchone.return_value = None

        with self.assertRaises(ValueError) as ctx:
            resolver.resolve_vin("UNKNOWN_VIN_12345")
        self.assertIn("Unknown VIN", str(ctx.exception))

    def test_timescale_sink_insert_mapping(self):
        sink = TimescaleSink("mock_conn_str")
        sink.conn = MagicMock()
        sink.conn.closed = False
        mock_cursor = MagicMock()
        mock_cursor.mogrify.return_value = b"INSERT INTO telemetry VALUES (...)"
        sink.conn.cursor.return_value.__enter__.return_value = mock_cursor

        sample_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "vehicle_id": "00000000-0000-0000-0000-000000000003",
            "vin": "1HGCM82633A004352",
            "oem_id": "OEM_A",
            "schema_version": "1.0",
            "event_ts": "2026-09-28T09:00:00.000Z",
            "ingested_at": "2026-09-28T09:00:00.050Z",
            "seq": 100,
            "event_type": "TELEMETRY",
            "vehicle_type": "EV",
            "location": {"lat": 37.7749, "lon": -122.4194},
            "speed_kmh": 45.0,
            "odometer_km": 12455.0,
            "acceleration_ms2": 0.5,
            "battery": {
                "soc_pct": 91.0,
                "voltage_v": 395.0,
                "current_a": 35.0,
                "temperature_c": 30.0,
                "charging": False,
            },
            "powertrain": {
                "rpm": None,
                "engine_temp_c": None,
                "motor_temp_c": 49.0,
                "power_kw": 13.82,
                "engine_load_pct": None,
            },
            "brakes": {"harsh_brake": False},
            "dtc_codes": [],
            "trip_id": None,
        }

        record = sink._event_to_record(sample_event)
        self.assertEqual(record["vehicle_id"], "00000000-0000-0000-0000-000000000003")
        self.assertEqual(record["speed_kmh"], 45.0)
        self.assertEqual(record["latitude"], 37.7749)
        self.assertEqual(record["soc_pct"], 91.0)
        self.assertEqual(record["motor_temp_c"], 49.0)
        self.assertFalse(record["harsh_brake"])
        self.assertEqual(record["dtc_codes"], [])

        sink.write_batch([sample_event])
        mock_cursor.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
