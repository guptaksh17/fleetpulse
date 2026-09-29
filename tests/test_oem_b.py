"""
OEM-B onboarding: the v2.0 payload satisfies its JSON-Schema contract, and the OEM-B adapter
produces the same canonical event as the OEM-A adapter for the same underlying reading
(within the rounding of unit conversions). No downstream code knows which OEM sent an event.
"""

import json
import math
import os
import sys
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "normalization"))
sys.path.insert(0, os.path.join(ROOT, "services", "identity-resolver"))

from simulator.config import load_config  # noqa: E402
from simulator.engine import FleetEngine  # noqa: E402
from simulator.serializers import OEMAEnvelopeSerializer, OEMBEnvelopeSerializer  # noqa: E402
from normalizer import AdapterFactory  # noqa: E402

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


def payloads(n_vehicles=8, hours=12):
    cfg = load_config("configs/offline_train.yaml")
    cfg.population.vehicles = n_vehicles
    eng = FleetEngine(cfg)
    out = []
    eng.run(eng.start_time + timedelta(hours=hours), lambda t, outs: out.extend((o.vehicle, o.payload) for o in outs))
    return out


class TestOemB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = payloads()
        cls.factory = AdapterFactory()

    def enrich(self, env, v):
        return {**env, "vehicle_id": v.vehicle_id, "vehicle_type": v.vehicle_type}

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_payload_matches_contract(self):
        schema = json.load(open(os.path.join(ROOT, "contracts", "oem", "oem_b_v2.schema.json")))
        for v, p in self.samples[:300]:
            jsonschema.validate(OEMBEnvelopeSerializer.to_payload(p), schema)

    def test_same_canonical_event_as_oem_a(self):
        a_adapter = self.factory.get_adapter("OEM_A", "1.0")
        b_adapter = self.factory.get_adapter("OEM_B", "2.0")
        self.assertIsNotNone(b_adapter)
        checked = moving = 0
        for v, p in self.samples:
            a = a_adapter.normalize(self.enrich(OEMAEnvelopeSerializer.serialize(p), v))
            b = b_adapter.normalize(self.enrich(OEMBEnvelopeSerializer.serialize(p, oem_id="OEM_B"), v))
            for k in ("vehicle_id", "vin", "seq", "event_ts", "event_type", "dtc_codes"):
                self.assertEqual(a[k], b[k], k)
            self.assertEqual(a["brakes"], b["brakes"])
            self.assertAlmostEqual(a["speed_kmh"], b["speed_kmh"], delta=0.01)
            self.assertAlmostEqual(a["odometer_km"], b["odometer_km"], delta=0.01)
            self.assertAlmostEqual(a["acceleration_ms2"], b["acceleration_ms2"], delta=0.01)
            for group in ("battery", "powertrain"):
                for k, av in a[group].items():
                    if k == "charging":
                        continue
                    bv = b[group][k]
                    if av is None:
                        self.assertIsNone(bv, f"{group}.{k}")
                    else:
                        self.assertTrue(math.isclose(av, bv, abs_tol=0.02), f"{group}.{k}: {av} vs {bv}")
            checked += 1
            moving += p["vehicleSpeed"] > 1
        self.assertGreater(checked, 500)
        self.assertGreater(moving, 100, "must cover driving samples, not only parked heartbeats")

    def test_resolver_finds_nested_vin(self):
        import resolver
        v, p = self.samples[0]
        payload = OEMBEnvelopeSerializer.to_payload(p)
        vin = payload.get("vin") or (payload.get("vehicle") or {}).get("vin")
        self.assertEqual(vin, p["vin"])
        self.assertTrue(resolver.is_valid_vin(vin))


if __name__ == "__main__":
    unittest.main()
