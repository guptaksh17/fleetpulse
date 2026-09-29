"""features_from_window_stats on hand-computed fixtures (Phase 4 B3)."""

import math
import unittest

from fleetpulse_features.features import (
    DAY_MS, feature_names, features_from_window_stats, window_complete,
)

T = 1_000 * DAY_MS  # arbitrary snapshot time (ms)


def ctx(vt, **kw):
    c = {"vehicle_type": vt, "feature_ts_ms": T, "first_event_ts_ms": T - 2 * DAY_MS,
         "manufacture_date_ms": T - 400 * DAY_MS, "last_service_ts_ms": None, "last_service_odometer_km": None}
    c.update(kw)
    return c


W1H = {
    "n_events": 4, "n_moving": 2, "speed_sum": 100.0, "speed_sumsq": 40.0 ** 2 + 60.0 ** 2,
    "odo_min": 1000.0, "odo_max": 1012.5,
    "harsh_count": 1, "harsh_high_speed_count": 0, "harsh_accel_n": 1, "harsh_accel_sum": -3.0,
    "accel_min": -3.0, "brake_n": 2, "brake_accel_sum": -5.0,
    "dtc_event_count": 1, "dtc_brake_count": 1,
    "engine_temp_n": 2, "engine_temp_sum": 200.0, "engine_temp_sumsq": 90.0 ** 2 + 110.0 ** 2,
    "engine_temp_max": 110.0, "engine_temp_min": 90.0, "engine_temp_high_count": 1,
    "motor_temp_n": 2, "motor_temp_sum": 150.0, "motor_temp_sumsq": 70.0 ** 2 + 80.0 ** 2,
    "motor_temp_max": 80.0, "motor_temp_min": 70.0,
    "power_n": 2, "power_sum": 90.0, "power_sumsq": 30.0 ** 2 + 60.0 ** 2, "power_max": 60.0, "power_min": 30.0,
    "battery_temp_n": 2, "battery_temp_sum": 70.0, "battery_temp_sumsq": 30.0 ** 2 + 40.0 ** 2,
    "battery_temp_max": 40.0, "battery_temp_min": 30.0,
    "voltage_n": 2, "voltage_sum": 780.0, "voltage_sumsq": 380.0 ** 2 + 400.0 ** 2, "voltage_max": 400.0, "voltage_min": 380.0,
    "current_n": 2, "current_sum": 20.0, "current_sumsq": (-30.0) ** 2 + 50.0 ** 2, "current_max": 50.0, "current_min": -30.0,
    "current_absmax": 50.0, "charging_count": 1,
    "soc_min": 40.0, "soc_max": 60.0, "soc_first": (1, 1, 60.0), "soc_last": (9, 9, 40.0),
}


class TestFeatureDefinitions(unittest.TestCase):
    def test_common_and_brake(self):
        f = features_from_window_stats({"1h": W1H}, ctx("ICE"), "BRAKE")
        self.assertEqual(f["w1h_events"], 4.0)
        self.assertEqual(f["w1h_moving_events"], 2.0)
        self.assertAlmostEqual(f["w1h_avg_speed_moving"], 50.0)
        self.assertAlmostEqual(f["w1h_speed_std_moving"], 10.0)
        self.assertAlmostEqual(f["w1h_distance_km"], 12.5)
        self.assertEqual(f["w1h_dtc_events"], 1.0)
        self.assertEqual(f["w1h_component_dtc_events"], 1.0)
        self.assertEqual(f["w1h_harsh_brakes"], 1.0)
        self.assertAlmostEqual(f["w1h_mean_harsh_decel"], 3.0)
        self.assertAlmostEqual(f["w1h_mean_braking_decel"], 2.5)
        self.assertEqual(f["w1h_min_accel"], -3.0)
        # Empty windows: counts are 0, means NaN
        self.assertEqual(f["w5m_events"], 0.0)
        self.assertTrue(math.isnan(f["w5m_avg_speed_moving"]))
        self.assertTrue(math.isnan(f["w24h_mean_harsh_decel"]))
        self.assertTrue(math.isnan(f["odometer_km"]), "no 24h data -> odometer unknown")

    def test_powertrain_ice_vs_ev_vs_hybrid(self):
        ice = features_from_window_stats({"1h": W1H}, ctx("ICE"), "POWERTRAIN")
        self.assertAlmostEqual(ice["w1h_engine_temp_mean"], 100.0)
        self.assertAlmostEqual(ice["w1h_engine_temp_std"], 10.0)
        self.assertEqual(ice["w1h_engine_temp_max"], 110.0)
        self.assertEqual(ice["w1h_high_engine_temp_events"], 1.0)
        self.assertTrue(math.isnan(ice["w1h_motor_temp_mean"]), "ICE has no traction motor")
        self.assertTrue(math.isnan(ice["w1h_power_mean"]))
        ev = features_from_window_stats({"1h": W1H}, ctx("EV"), "POWERTRAIN")
        self.assertTrue(math.isnan(ev["w1h_engine_temp_mean"]), "EV has no engine")
        self.assertTrue(math.isnan(ev["w1h_high_engine_temp_events"]))
        self.assertAlmostEqual(ev["w1h_motor_temp_mean"], 75.0)
        self.assertAlmostEqual(ev["w1h_motor_temp_std"], 5.0)
        self.assertAlmostEqual(ev["w1h_power_mean"], 45.0)
        self.assertEqual(ev["w1h_high_power_events"], 0.0)
        hy = features_from_window_stats({"1h": W1H}, ctx("HYBRID"), "POWERTRAIN")
        self.assertAlmostEqual(hy["w1h_engine_temp_mean"], 100.0)
        self.assertAlmostEqual(hy["w1h_motor_temp_mean"], 75.0)

    def test_battery(self):
        f = features_from_window_stats({"1h": W1H}, ctx("EV"), "BATTERY")
        self.assertEqual(f["w1h_soc_min"], 40.0)
        self.assertEqual(f["w1h_soc_max"], 60.0)
        self.assertAlmostEqual(f["w1h_soc_delta"], -20.0)
        self.assertAlmostEqual(f["w1h_battery_temp_mean"], 35.0)
        self.assertAlmostEqual(f["w1h_battery_temp_std"], 5.0)
        self.assertEqual(f["w1h_high_temp_events"], 0.0)
        self.assertAlmostEqual(f["w1h_voltage_mean"], 390.0)
        self.assertAlmostEqual(f["w1h_voltage_std"], 10.0)
        self.assertEqual(f["w1h_voltage_min"], 380.0)
        self.assertAlmostEqual(f["w1h_current_mean"], 10.0)
        self.assertAlmostEqual(f["w1h_current_std"], 40.0)
        self.assertEqual(f["w1h_current_max_abs"], 50.0)
        self.assertEqual(f["w1h_charging_events"], 1.0)
        self.assertEqual(f["w1h_fast_charge_events"], 0.0)
        self.assertEqual(f["w1h_deep_discharge_events"], 0.0)
        self.assertTrue(math.isnan(f["w5m_soc_delta"]))
        self.assertTrue(math.isnan(f["w5m_charging_events"]), "no current reported -> unknown, not zero")

    def test_static_context(self):
        s = ctx("EV", last_service_ts_ms=T - 3 * DAY_MS, last_service_odometer_km=1000.0)
        f = features_from_window_stats({"24h": {"odo_max": 1500.0, "odo_min": 1400.0, "n_events": 3}}, s, "BRAKE")
        self.assertAlmostEqual(f["days_since_service"], 3.0)
        self.assertEqual(f["has_service_history"], 1.0)
        self.assertAlmostEqual(f["distance_since_service_km"], 500.0)
        self.assertAlmostEqual(f["vehicle_age_days"], 400.0)
        self.assertEqual(f["odometer_km"], 1500.0)
        self.assertEqual((f["vt_ice"], f["vt_ev"], f["vt_hybrid"]), (0.0, 1.0, 0.0))
        n = features_from_window_stats({}, ctx("ICE"), "BRAKE")
        self.assertTrue(math.isnan(n["days_since_service"]))
        self.assertEqual(n["has_service_history"], 0.0)
        self.assertTrue(math.isnan(n["distance_since_service_km"]))

    def test_std_clamped_at_zero(self):
        # Constant signal whose running sums produce a tiny negative variance in float64.
        v = 0.1
        n = 3
        stats = {"engine_temp_n": n, "engine_temp_sum": v * n, "engine_temp_sumsq": (v * v) * n * (1 - 1e-15)}
        f = features_from_window_stats({"1h": stats}, ctx("ICE"), "POWERTRAIN")
        self.assertEqual(f["w1h_engine_temp_std"], 0.0)

    def test_window_complete(self):
        self.assertTrue(window_complete(ctx("ICE", first_event_ts_ms=T - DAY_MS)))
        self.assertFalse(window_complete(ctx("ICE", first_event_ts_ms=T - DAY_MS + 1)))

    def test_names_stable_and_identical_across_types(self):
        for comp in ("BRAKE", "POWERTRAIN", "BATTERY"):
            names = feature_names(comp)
            self.assertEqual(len(names), len(set(names)))
            for vt in ("ICE", "EV", "HYBRID"):
                self.assertEqual(list(features_from_window_stats({}, ctx(vt), comp).keys()), names)


if __name__ == "__main__":
    unittest.main()
