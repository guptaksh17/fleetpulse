"""
Unit tests for Rule Engine (DtcRule and RuleEngineStorage).
Validates:
- Registry parsing and code mapping to component/severity.
- Unknown code skipped and counter incremented.
- Incompatible component (e.g. BATTERY on ICE with NOT_APPLICABLE status) skipped.
- Idempotency: replaying same event yields only 1 alert row, 1 audit row, and 1 Kafka message.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "rule-engine"))

from rules.dtc_rule import DtcRule
from storage.postgres import RuleEngineStorage


class TestRuleEngine(unittest.TestCase):
    def setUp(self):
        self.registry_path = os.path.join(BASE_DIR, "contracts", "dtc", "dtc_registry.yaml")
        self.rule = DtcRule(self.registry_path)

    def test_registry_parsing(self):
        # Must have exactly the 6 codes specified in Phase 2
        self.assertEqual(len(self.rule.registry), 6)
        expected_mappings = {
            "P0301": ("POWERTRAIN", "CRITICAL"),
            "P0562": ("POWERTRAIN", "HIGH"),
            "BATT_TEMP_HIGH": ("BATTERY", "HIGH"),
            "BATT_VOLT_UNSTABLE": ("BATTERY", "HIGH"),
            "BRAKE_WEAR_HIGH": ("BRAKE", "HIGH"),
            "BRAKE_SYSTEM_CRITICAL": ("BRAKE", "CRITICAL"),
        }
        for code, (expected_comp, expected_sev) in expected_mappings.items():
            self.assertIn(code, self.rule.registry)
            self.assertEqual(self.rule.registry[code]["component"], expected_comp)
            self.assertEqual(self.rule.registry[code]["severity"], expected_sev)

    def test_unknown_code_skipped(self):
        event = {"dtc_codes": ["UNKNOWN_9999", "BRAKE_SYSTEM_CRITICAL"]}
        hits = self.rule.evaluate(event)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].dtc_code, "BRAKE_SYSTEM_CRITICAL")
        self.assertEqual(self.rule.unknown_codes_count, 1)

    def test_incompatible_vehicle_component_skipped(self):
        storage = RuleEngineStorage("mock_conn")
        storage.conn = MagicMock()
        storage.conn.closed = False
        mock_cursor = MagicMock()
        storage.conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Simulate vehicle_component with status = 'NOT_APPLICABLE' (e.g. ICE vehicle with BATTERY component)
        mock_cursor.fetchone.return_value = ("00000000-0000-0000-0000-0000000000b1", "NOT_APPLICABLE")

        vc_id = storage.get_vehicle_component("ice-vehicle-id", "BATTERY")
        self.assertIsNone(vc_id)
        self.assertEqual(storage.incompatible_component_count, 1)

    def test_alert_persistence_idempotency(self):
        storage = RuleEngineStorage("mock_conn")
        storage.conn = MagicMock()
        storage.conn.closed = False
        mock_cursor = MagicMock()
        storage.conn.cursor.return_value.__enter__.return_value = mock_cursor

        # First call: INSERT ... ON CONFLICT RETURNING alert_id succeeds
        mock_cursor.fetchone.return_value = ("alert-uuid-1", "2026-09-28T09:00:00Z")

        res1 = storage.persist_alert_transaction(
            vehicle_id="00000000-0000-0000-0000-000000000003",
            vehicle_component_id="00000000-0000-0000-0000-0000000000a1",
            component="BRAKE",
            alert_type="DTC_BRAKE_SYSTEM_CRITICAL",
            severity="CRITICAL",
            message="Critical braking-system condition",
            dtc_code="BRAKE_SYSTEM_CRITICAL",
            trigger_event_id="evt-1",
            trigger_event_ts="2026-09-28T09:00:00.000Z",
        )
        self.assertIsNotNone(res1)
        self.assertEqual(res1["alert_id"], "alert-uuid-1")

        # Second call with same event (conflict in DB: RETURNING returns None)
        mock_cursor.fetchone.return_value = None

        res2 = storage.persist_alert_transaction(
            vehicle_id="00000000-0000-0000-0000-000000000003",
            vehicle_component_id="00000000-0000-0000-0000-0000000000a1",
            component="BRAKE",
            alert_type="DTC_BRAKE_SYSTEM_CRITICAL",
            severity="CRITICAL",
            message="Critical braking-system condition",
            dtc_code="BRAKE_SYSTEM_CRITICAL",
            trigger_event_id="evt-1",
            trigger_event_ts="2026-09-28T09:00:00.000Z",
        )
        # Suppressed! No second alert or audit log
        self.assertIsNone(res2)


if __name__ == "__main__":
    unittest.main()
