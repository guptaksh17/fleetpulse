#!/usr/bin/env python3
"""
Unit and Contract Tests for FleetPulse Phase 2.
Tests OEM-A payload generation, stateless adapter mapping, and canonical schema adherence.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone

# Add workspace paths to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "simulator"))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "normalization"))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "identity-resolver"))

from vehicle_simulator import VehicleSimulator
from normalizer import AdapterFactory, OEMAAdapterV1


class TestContractsAndMapping(unittest.TestCase):
    def setUp(self):
        self.canonical_schema_path = os.path.join(
            BASE_DIR, "contracts", "canonical", "vehicle_event.schema.json"
        )
        self.oem_a_schema_path = os.path.join(
            BASE_DIR, "contracts", "oem", "oem_a_v1.schema.json"
        )
        with open(self.canonical_schema_path, "r", encoding="utf-8") as f:
            self.canonical_schema = json.load(f)
        with open(self.oem_a_schema_path, "r", encoding="utf-8") as f:
            self.oem_a_schema = json.load(f)

    def test_schemas_exist_and_parse(self):
        self.assertIn("CanonicalVehicleEvent", self.canonical_schema["title"])
        self.assertIn("OEMAEventV1", self.oem_a_schema["title"])
        self.assertEqual(len(self.canonical_schema["required"]), 19)
        self.assertEqual(len(self.oem_a_schema["required"]), 16)  # Includes longitudinalAccel in Phase 2

    def test_simulator_generates_valid_oem_a_payload(self):
        sim = VehicleSimulator()
        envelope = sim.next_step()

        self.assertEqual(envelope["oem_id"], "OEM_A")
        self.assertEqual(envelope["schema_version"], "1.0")
        self.assertIn("received_at", envelope)
        self.assertNotIn("vehicle_id", envelope)

        payload = envelope["payload"]
        for required_field in self.oem_a_schema["required"]:
            self.assertIn(
                required_field,
                payload,
                f"Missing required OEM-A field: {required_field}",
            )

        self.assertEqual(payload["vin"], "1HGCM82633A004352")
        self.assertEqual(payload["sequence"], 1)
        self.assertIsNone(payload["engineRpm"])
        self.assertEqual(payload["faultCodes"], [])
        self.assertIn(payload["event"], ["TELEMETRY", "HARSH_BRAKE", "DTC"])
        self.assertIsInstance(payload["longitudinalAccel"], (int, float))
        self.assertGreaterEqual(payload["batteryLevel"], 0.0)
        self.assertLessEqual(payload["batteryLevel"], 100.0)

    def test_oem_a_adapter_stateless_mapping(self):
        adapter = OEMAAdapterV1()

        raw_envelope = {
            "received_at": "2026-09-28T09:00:00.000Z",
            "oem_id": "OEM_A",
            "schema_version": "1.0",
            "vehicle_id": "00000000-0000-0000-0000-000000000003",
            "vehicle_type": "EV",
            "payload": {
                "vin": "1HGCM82633A004352",
                "ts": "2026-09-28T09:00:00.000Z",
                "lat": 37.774929,
                "lng": -122.419416,
                "vehicleSpeed": 65.5,
                "mileageKm": 12450.789,
                "batteryLevel": 88.2,
                "batteryVoltage": 395.4,
                "batteryCurrent": 55.0,
                "batteryTemp": 31.2,
                "engineRpm": None,
                "motorTemperature": 52.8,
                "faultCodes": [],
                "longitudinalAccel": 1.25,
                "event": "TELEMETRY",
                "sequence": 42,
            },
        }

        canonical = adapter.normalize(raw_envelope)

        # Verify exact field mappings
        self.assertEqual(canonical["vehicle_id"], "00000000-0000-0000-0000-000000000003")
        self.assertEqual(canonical["vin"], "1HGCM82633A004352")
        self.assertEqual(canonical["oem_id"], "OEM_A")
        self.assertEqual(canonical["schema_version"], "1.0")
        self.assertEqual(canonical["event_ts"], "2026-09-28T09:00:00.000Z")
        self.assertEqual(canonical["seq"], 42)
        self.assertEqual(canonical["event_type"], "TELEMETRY")
        self.assertEqual(canonical["vehicle_type"], "EV")

        # Location mapping
        self.assertEqual(canonical["location"]["lat"], 37.774929)
        self.assertEqual(canonical["location"]["lon"], -122.419416)

        # Metrics mapping
        self.assertEqual(canonical["speed_kmh"], 65.5)
        self.assertEqual(canonical["odometer_km"], 12450.789)
        self.assertEqual(canonical["acceleration_ms2"], 1.25)  # Mapped directly from longitudinalAccel
        self.assertEqual(canonical["battery"]["soc_pct"], 88.2)
        self.assertEqual(canonical["battery"]["voltage_v"], 395.4)
        self.assertEqual(canonical["battery"]["current_a"], 55.0)
        self.assertEqual(canonical["battery"]["temperature_c"], 31.2)
        self.assertFalse(canonical["battery"]["charging"])

        # Powertrain mapping
        self.assertIsNone(canonical["powertrain"]["rpm"])
        self.assertEqual(canonical["powertrain"]["motor_temp_c"], 52.8)
        self.assertIsNone(canonical["powertrain"]["engine_temp_c"])
        self.assertAlmostEqual(canonical["powertrain"]["power_kw"], (395.4 * 55.0) / 1000.0, places=2)

        # Brakes & DTC
        self.assertFalse(canonical["brakes"]["harsh_brake"])
        self.assertEqual(canonical["dtc_codes"], [])
        self.assertIsNone(canonical["trip_id"])

        # Check all canonical required fields exist
        for req in self.canonical_schema["required"]:
            self.assertIn(req, canonical, f"Missing canonical field: {req}")

    def test_harsh_brake_mapping(self):
        adapter = OEMAAdapterV1()
        envelope = {
            "received_at": "2026-09-28T09:00:00.000Z",
            "oem_id": "OEM_A",
            "schema_version": "1.0",
            "vehicle_id": "00000000-0000-0000-0000-000000000003",
            "vehicle_type": "EV",
            "payload": {
                "vin": "1HGCM82633A004352",
                "ts": "2026-09-28T09:00:00.000Z",
                "lat": 37.774929,
                "lng": -122.419416,
                "vehicleSpeed": 10.0,
                "mileageKm": 12450.8,
                "batteryLevel": 88.0,
                "batteryVoltage": 395.0,
                "batteryCurrent": -18.0,
                "batteryTemp": 31.2,
                "engineRpm": None,
                "motorTemperature": 52.0,
                "faultCodes": [],
                "longitudinalAccel": -6.5,
                "event": "HARSH_BRAKE",
                "sequence": 50,
            },
        }
        canonical = adapter.normalize(envelope)
        self.assertTrue(canonical["brakes"]["harsh_brake"])
        self.assertEqual(canonical["event_type"], "TELEMETRY")  # Canonical type stays TELEMETRY

    def test_dtc_event_mapping(self):
        adapter = OEMAAdapterV1()
        envelope = {
            "received_at": "2026-09-28T09:00:00.000Z",
            "oem_id": "OEM_A",
            "schema_version": "1.0",
            "vehicle_id": "00000000-0000-0000-0000-000000000003",
            "vehicle_type": "EV",
            "payload": {
                "vin": "1HGCM82633A004352",
                "ts": "2026-09-28T09:00:00.000Z",
                "lat": 37.774929,
                "lng": -122.419416,
                "vehicleSpeed": 50.0,
                "mileageKm": 12451.0,
                "batteryLevel": 87.5,
                "batteryVoltage": 394.0,
                "batteryCurrent": 40.0,
                "batteryTemp": 31.5,
                "engineRpm": None,
                "motorTemperature": 52.5,
                "faultCodes": ["BRAKE_SYSTEM_CRITICAL"],
                "longitudinalAccel": 0.0,
                "event": "DTC",
                "sequence": 60,
            },
        }
        canonical = adapter.normalize(envelope)
        self.assertEqual(canonical["event_type"], "DTC")
        self.assertEqual(canonical["dtc_codes"], ["BRAKE_SYSTEM_CRITICAL"])
        self.assertFalse(canonical["brakes"]["harsh_brake"])

    def test_canonical_event_deep_schema_adherence(self):
        adapter = OEMAAdapterV1()
        envelope = {
            "received_at": "2026-09-28T09:00:00.000Z",
            "oem_id": "OEM_A",
            "schema_version": "1.0",
            "vehicle_id": "00000000-0000-0000-0000-000000000003",
            "vehicle_type": "EV",
            "payload": {
                "vin": "1HGCM82633A004352",
                "ts": "2026-09-28T09:00:00.000Z",
                "lat": 37.774929,
                "lng": -122.419416,
                "vehicleSpeed": 45.0,
                "mileageKm": 12450.0,
                "batteryLevel": 90.0,
                "batteryVoltage": 395.0,
                "batteryCurrent": 40.0,
                "batteryTemp": 30.0,
                "engineRpm": None,
                "motorTemperature": 50.0,
                "faultCodes": [],
                "longitudinalAccel": 0.5,
                "event": "TELEMETRY",
                "sequence": 1,
            },
        }
        event = adapter.normalize(envelope)

        props = self.canonical_schema["properties"]
        for prop, rule in props.items():
            self.assertIn(prop, event, f"Missing property: {prop}")
            val = event[prop]
            rule_types = rule.get("type")
            if isinstance(rule_types, str):
                rule_types = [rule_types]
            if "enum" in rule:
                self.assertIn(val, rule["enum"])
            if val is not None:
                if "string" in rule_types:
                    self.assertIsInstance(val, str)
                    if "minLength" in rule:
                        self.assertGreaterEqual(len(val), rule["minLength"])
                    if "maxLength" in rule:
                        self.assertLessEqual(len(val), rule["maxLength"])
                elif "number" in rule_types:
                    self.assertIsInstance(val, (int, float))
                elif "integer" in rule_types:
                    self.assertIsInstance(val, int)
                elif "boolean" in rule_types:
                    self.assertIsInstance(val, bool)
                elif "object" in rule_types:
                    self.assertIsInstance(val, dict)
                    for sub_prop in rule.get("required", []):
                        self.assertIn(sub_prop, val)
                elif "array" in rule_types:
                    self.assertIsInstance(val, list)

    def test_adapter_factory(self):
        factory = AdapterFactory()
        adapter = factory.get_adapter("OEM_A", "1.0")
        self.assertIsNotNone(adapter)
        self.assertIsInstance(adapter, OEMAAdapterV1)

        self.assertIsNone(factory.get_adapter("OEM_B", "1.0"))
        self.assertIsNone(factory.get_adapter("OEM_A", "2.0"))

    def test_oem_a_v1_extended_fields_mapping(self):
        adapter = OEMAAdapterV1()
        raw_envelope = {
            "received_at": "2026-09-28T09:00:00.000Z",
            "oem_id": "OEM_A",
            "schema_version": "1.0",
            "vehicle_id": "00000000-0000-0000-0000-000000000003",
            "vehicle_type": "HYBRID",
            "payload": {
                "vin": "1HGCM82633A004352",
                "ts": "2026-09-28T09:00:00.000Z",
                "lat": 13.0827,
                "lng": 80.2707,
                "vehicleSpeed": 72.0,
                "mileageKm": 12455.0,
                "batteryLevel": 65.0,
                "batteryVoltage": 350.0,
                "batteryCurrent": -40.0,
                "batteryTemp": 32.5,
                "engineRpm": 2200.0,
                "motorTemperature": 58.0,
                "faultCodes": [],
                "longitudinalAccel": -0.85,
                "event": "TELEMETRY",
                "sequence": 105,
                "engineTemp": 92.4,
                "engineLoadPct": 45.6,
                "powerKw": 35.8,
                "charging": True,
                "tripRef": "trip-uuid-12345",
            },
        }

        canonical = adapter.normalize(raw_envelope)
        self.assertEqual(canonical["powertrain"]["engine_temp_c"], 92.4)
        self.assertEqual(canonical["powertrain"]["engine_load_pct"], 45.6)
        self.assertEqual(canonical["powertrain"]["power_kw"], 35.8)
        self.assertTrue(canonical["battery"]["charging"])
        self.assertEqual(canonical["trip_id"], "trip-uuid-12345")


if __name__ == "__main__":
    unittest.main()
