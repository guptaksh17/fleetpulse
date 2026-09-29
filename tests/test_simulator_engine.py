"""
Phase 4 Part A tests for the shared simulation engine:
  - offline generator events equal calibration events for the same config, seed and period (A1)
  - resume from an on-disk checkpoint is byte-identical to a continuous run (A3)
  - maintenance events carry deterministic ids, component ids and odometer readings (A1, A4)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from simulator.config import load_config
from simulator.runner_offline import run_offline_simulation
from simulator.sinks import InMemorySink
import calibrate_degradation
import verify_resume_equivalence

CONFIG = "configs/offline_train.yaml"


class TestFleetEngine(unittest.TestCase):
    def test_offline_events_equal_calibration_events(self):
        vehicles, days = 60, 30
        cfg = load_config(CONFIG)
        tel, gt = InMemorySink(keep_telemetry=False), InMemorySink()
        stats = run_offline_simulation(cfg, tel, gt, days=days, vehicles_override=vehicles)
        offline = [e.to_dict() for e in gt.events]

        cfg2 = load_config(CONFIG)
        cfg2.population.vehicles = vehicles
        calib = calibrate_degradation.collect_events(cfg2, days)

        self.assertGreater(len(offline), 0, "offline generator must persist maintenance events")
        self.assertEqual(offline, calib)
        self.assertEqual(stats["maintenance_events"], len(offline))
        types = {e["event_type"] for e in offline}
        self.assertTrue(types & {"MAINTENANCE_REQUIRED", "FAILURE"})
        for e in offline:
            self.assertIsNotNone(e["event_id"])
            self.assertIsNotNone(e["vehicle_component_id"])
            self.assertIn(e["component"], ("BRAKE", "POWERTRAIN", "BATTERY"))
            self.assertIsInstance(e["odometer_km"], float)
            self.assertTrue(e["occurred_at"].startswith("2026-"), "start time must come from config")
        self.assertEqual(len({e["event_id"] for e in offline}), len(offline))

    def test_resume_equivalence(self):
        with tempfile.TemporaryDirectory() as d:
            res = verify_resume_equivalence.run(CONFIG, vehicles=8, days=4, checkpoint_path=os.path.join(d, "cp.json"))
        self.assertGreater(res["continuous"]["telemetry_lines"], 0)
        self.assertEqual(res["continuous"], res["resumed"])
        self.assertTrue(res["identical"])


class TestPopulationsDisjoint(unittest.TestCase):
    def test_live_demo_and_offline_vehicle_ids_are_disjoint(self):
        from simulator.config import SimulationRNG
        from simulator.population import FleetPopulation

        def ids(path, n=None):
            cfg = load_config(path)
            if n:
                cfg.population.vehicles = n
            return {v.vehicle_id for v in FleetPopulation.generate_population(cfg, SimulationRNG(cfg.seed).population)}

        self.assertEqual(ids(CONFIG) & ids("configs/live_demo.yaml", 500), set())


if __name__ == "__main__":
    unittest.main()
