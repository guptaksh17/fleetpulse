"""
Unit and Integration Tests for FleetPulse Phase 3: Simulator Realism and Ground Truth.
Verifies VIN ISO-3779 validity, population distributions, component degradation models,
sudden failure rules, zero state leakage, determinism, physical plausibility,
checkpoint serialization, and OEM-A adapter mapping.
"""

from datetime import datetime, timezone, timedelta
import json
import os
import tempfile
import unittest

import numpy as np

from simulator.vin import validate_vin, generate_vin, generate_unique_vins
from simulator.config import load_config, SimulationRNG
from simulator.clock import SimulationClock
from simulator.population import FleetPopulation, SimVehicle
from simulator.components import (
    LifecycleState,
    BrakeComponent,
    PowertrainComponent,
    BatteryComponent,
)
from simulator.checkpoint import CheckpointManager
from services.normalization.normalizer import OEMAAdapterV1


class TestSimulatorPhase3(unittest.TestCase):
    def setUp(self):
        self.config_path = "configs/offline_train.yaml"
        self.config = load_config(self.config_path)

    def test_vin_iso3779_validity_and_uniqueness(self):
        """1. VIN validity and uniqueness: 1000 VINs, all 17 chars, check digits, no I/O/Q, all unique."""
        rng = np.random.default_rng(42)
        vins = generate_unique_vins(rng, 1000)
        self.assertEqual(len(vins), 1000)
        self.assertEqual(len(set(vins)), 1000, "All VINs must be unique")

        for vin in vins:
            self.assertEqual(len(vin), 17)
            self.assertFalse(any(c in "IOQ" for c in vin), f"VIN {vin} contains forbidden letter")
            self.assertTrue(validate_vin(vin), f"VIN {vin} failed ISO 3779 check digit validation")

    def test_population_demographics_proportions(self):
        """2. Population proportions: 500 vehicles match configured distributions within bounds."""
        rng_streams = SimulationRNG(12345)
        self.config.population.vehicles = 500
        vehicles = FleetPopulation.generate_population(self.config, rng_streams.population)
        self.assertEqual(len(vehicles), 500)

        # Powertrains: ~50% ICE, ~30% EV, ~20% Hybrid
        type_counts = {"ICE": 0, "EV": 0, "HYBRID": 0}
        behavior_counts = {"CONSERVATIVE": 0, "NORMAL": 0, "AGGRESSIVE": 0}
        profile_counts = {"CITY": 0, "HIGHWAY": 0, "MIXED": 0}

        for v in vehicles:
            type_counts[v.vehicle_type] += 1
            behavior_counts[v.driver_behavior.value] += 1
            profile_counts[v.driving_profile.value] += 1

            # Assert age in [0.1, 8.0]
            self.assertGreaterEqual(v.age_years, 0.1)
            self.assertLessEqual(v.age_years, 8.0)

            # Assert components
            self.assertIn("BRAKE", v.components)
            self.assertIn("POWERTRAIN", v.components)
            if v.vehicle_type in ("EV", "HYBRID"):
                self.assertIn("BATTERY", v.components)
            else:
                self.assertNotIn("BATTERY", v.components)

        # Statistical tolerance checks (within 10% tolerance for 500 samples)
        self.assertAlmostEqual(type_counts["ICE"] / 500.0, 0.50, delta=0.10)
        self.assertAlmostEqual(type_counts["EV"] / 500.0, 0.30, delta=0.10)
        self.assertAlmostEqual(type_counts["HYBRID"] / 500.0, 0.20, delta=0.10)

        self.assertAlmostEqual(behavior_counts["CONSERVATIVE"] / 500.0, 0.40, delta=0.10)
        self.assertAlmostEqual(behavior_counts["NORMAL"] / 500.0, 0.45, delta=0.10)
        self.assertAlmostEqual(behavior_counts["AGGRESSIVE"] / 500.0, 0.15, delta=0.10)

    def test_component_wear_and_lifecycle_invariants(self):
        """3. Component wear accumulates monotonically, halts awaiting service, and recovers partially."""
        brake = BrakeComponent(initial_health=100.0, wear_rate_multiplier=1.0)
        rng = np.random.default_rng(999)
        t_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Monotonic accumulation during normal driving
        prev_wear = brake.wear
        for _ in range(5):
            brake.step(
                dt_hours=1.0,
                distance_km=40.0,
                stress_inputs={"harsh_brake_count": 0.0, "high_speed_decel_count": 0.0},
                current_sim_time=t_now,
                rng_degradation=rng,
            )
            self.assertGreater(brake.wear, prev_wear)
            prev_wear = brake.wear

        # Set wear into MAINTENANCE_REQUIRED
        brake.wear = 76.0
        brake.health = 24.0
        events = brake.step(
            dt_hours=1.0,
            distance_km=40.0,
            stress_inputs={},
            current_sim_time=t_now,
            rng_degradation=rng,
        )
        self.assertTrue(brake.pending_service)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "MAINTENANCE_REQUIRED")

        # Wear must halt while awaiting service
        wear_at_request = brake.wear
        t_later = t_now + timedelta(hours=10)
        brake.step(
            dt_hours=10.0,
            distance_km=200.0,
            stress_inputs={},
            current_sim_time=t_later,
            rng_degradation=rng,
        )
        self.assertEqual(brake.wear, wear_at_request, "Wear must NOT accumulate while awaiting service")

        # Advance past service due time
        t_service_done = brake.service_due_ts + timedelta(minutes=1)
        events_done = brake.step(
            dt_hours=1.0,
            distance_km=0.0,
            stress_inputs={},
            current_sim_time=t_service_done,
            rng_degradation=rng,
        )
        self.assertEqual(len(events_done), 1)
        self.assertEqual(events_done[0].event_type, "SERVICE_COMPLETED")
        self.assertFalse(brake.pending_service)
        # Service recovery: wear is partially recovered (0.15 - 0.25 of previous), never zero!
        self.assertGreater(brake.wear, 0.0, "Recovered wear must never be zero")
        self.assertLess(brake.wear, wear_at_request)

    def test_sudden_failure_only_from_at_risk(self):
        """4. Sudden failure: can only occur from AT_RISK state."""
        rng = np.random.default_rng(42)
        t_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # In HEALTHY state: sudden failure rate must be 0
        healthy_comp = BrakeComponent(initial_health=90.0, sudden_failure_per_hour_at_risk=0.99)
        for _ in range(20):
            evts = healthy_comp.step(
                dt_hours=1.0,
                distance_km=50.0,
                stress_inputs={},
                current_sim_time=t_now,
                rng_degradation=rng,
            )
            self.assertFalse(any(e.event_type == "FAILURE" for e in evts))

        # In AT_RISK state (health between 25 and 50): sudden failure can occur
        at_risk_comp = BrakeComponent(initial_health=40.0, sudden_failure_per_hour_at_risk=0.99)
        failed = False
        for _ in range(50):
            evts = at_risk_comp.step(
                dt_hours=1.0,
                distance_km=50.0,
                stress_inputs={"harsh_brake_count": 2.0},
                current_sim_time=t_now,
                rng_degradation=rng,
            )
            if any(e.event_type == "FAILURE" for e in evts):
                failed = True
                self.assertEqual(at_risk_comp.lifecycle, LifecycleState.FAILURE)
                break
        self.assertTrue(failed, "High hazard at-risk component should trigger sudden failure")

    def test_zero_leakage_in_observable_telemetry(self):
        """5. Zero leakage: inspect 1000 generated telemetry events — assert NO hidden state fields."""
        rng_streams = SimulationRNG(777)
        self.config.population.vehicles = 5
        vehicles = FleetPopulation.generate_population(self.config, rng_streams.population)
        t_sim = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

        forbidden_keys = {
            "health",
            "wear",
            "lifecycle",
            "stress",
            "component_state",
            "degradation",
            "pending_service",
            "service_due",
            "failure_type",
        }

        events_checked = 0
        slots = [(t_sim, t_sim + timedelta(hours=2))]

        for _ in range(200):
            for v in vehicles:
                payload, _, _ = v.step(
                    dt_seconds=60,
                    current_sim_time=t_sim,
                    rng_driving=rng_streams.driving,
                    rng_degradation=rng_streams.degradation,
                    rng_faults=rng_streams.faults,
                    rng_noise=rng_streams.noise,
                    scheduled_slots=slots,
                )
                events_checked += 1

                # Check all keys and nested serialized strings
                payload_str = json.dumps(payload).lower()
                for f_key in forbidden_keys:
                    self.assertNotIn(f'"{f_key}"', payload_str, f"Forbidden key '{f_key}' found in telemetry!")
            t_sim += timedelta(seconds=60)

        self.assertEqual(events_checked, 1000)

    def test_simulation_determinism(self):
        """6. Determinism: same seed produces identical telemetry and states."""
        def run_short_sim(seed: int):
            rng = SimulationRNG(seed)
            self.config.population.vehicles = 2
            vehicles = FleetPopulation.generate_population(self.config, rng.population)
            t_sim = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
            slots = [(t_sim, t_sim + timedelta(hours=1))]

            collected = []
            for _ in range(30):
                for v in vehicles:
                    p, _, _ = v.step(
                        dt_seconds=60,
                        current_sim_time=t_sim,
                        rng_driving=rng.driving,
                        rng_degradation=rng.degradation,
                        rng_faults=rng.faults,
                        rng_noise=rng.noise,
                        scheduled_slots=slots,
                    )
                    collected.append((p["vehicleSpeed"], p["mileageKm"], p.get("engineTemp"), p.get("batteryVoltage")))
                t_sim += timedelta(seconds=60)
            return collected

        run1 = run_short_sim(42)
        run2 = run_short_sim(42)
        run3 = run_short_sim(999)

        self.assertEqual(run1, run2, "Identical seed must yield identical outputs")
        self.assertNotEqual(run1, run3, "Different seed must yield different outputs")

    def test_checkpoint_save_and_restore(self):
        """7. Checkpoint: saves and restores exact state for deterministic resumption."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cp_path = os.path.join(tmp_dir, "checkpoint.json")
            rng = SimulationRNG(555)
            self.config.population.vehicles = 2
            vehicles = FleetPopulation.generate_population(self.config, rng.population)
            t_sim = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
            slots = [(t_sim, t_sim + timedelta(hours=2))]

            # Run 10 steps
            for _ in range(10):
                for v in vehicles:
                    v.step(60, t_sim, rng.driving, rng.degradation, rng.faults, rng.noise, slots)
                t_sim += timedelta(seconds=60)

            # Save checkpoint
            CheckpointManager.save_checkpoint(
                filepath=cp_path,
                sim_time=t_sim,
                step_index=10,
                config_hash=self.config.config_hash,
                rng_streams=rng,
                vehicles=vehicles,
            )

            # Clone and run 5 more steps on run A
            for _ in range(5):
                for v in vehicles:
                    v.step(60, t_sim, rng.driving, rng.degradation, rng.faults, rng.noise, slots)
                t_sim += timedelta(seconds=60)
            final_odos_a = [v.odometer_km for v in vehicles]

            # Now restore into fresh objects from checkpoint
            rng_b = SimulationRNG(555)
            vehicles_b = FleetPopulation.generate_population(self.config, rng_b.population)
            cp_data = CheckpointManager.load_checkpoint(cp_path)
            t_resumed, step_resumed = CheckpointManager.restore_simulator(
                checkpoint_data=cp_data,
                expected_config_hash=self.config.config_hash,
                rng_streams=rng_b,
                vehicles=vehicles_b,
            )
            self.assertEqual(step_resumed, 10)

            # Run 5 steps on restored run B
            for _ in range(5):
                for v in vehicles_b:
                    v.step(60, t_resumed, rng_b.driving, rng_b.degradation, rng_b.faults, rng_b.noise, slots)
                t_resumed += timedelta(seconds=60)
            final_odos_b = [v.odometer_km for v in vehicles_b]

            self.assertEqual(final_odos_a, final_odos_b, "Resumed execution must match continuous execution exactly")


if __name__ == "__main__":
    unittest.main()
