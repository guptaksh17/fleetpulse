"""Bucket statistic extraction and merging (Phase 4 B2)."""

import math
import random
import unittest

import numpy as np

from fleetpulse_features.config import load_feature_config
from fleetpulse_features.stats import (
    STAT_SPEC, COUNT, SUM, MIN, MAX, FIRST, LAST,
    columns_from_records, extract_stats, merge_buckets, single_event_bucket,
)

FCFG = load_feature_config()


def rec(ts, seq, **kw):
    base = {
        "vehicle_id": "v1", "event_ts_ms": ts, "seq": seq, "speed_kmh": 50.0, "odometer_km": 1000.0,
        "acceleration_ms2": 0.0, "harsh_brake": False, "dtc_codes": [], "soc_pct": None, "voltage_v": None,
        "current_a": None, "temperature_c": None, "motor_temp_c": None, "engine_temp_c": None,
        "power_kw": None, "engine_load_pct": None, "rpm": None,
    }
    base.update(kw)
    return base


def buckets_for(records):
    cols = columns_from_records(records)
    st = extract_stats(cols, FCFG.thresholds)
    return [single_event_bucket(st, i, r["event_ts_ms"], r["seq"]) for i, r in enumerate(records)]


class TestStats(unittest.TestCase):
    def test_counts_sums_and_moving(self):
        b = merge_buckets(buckets_for([
            rec(0, 1, speed_kmh=0.0),
            rec(1000, 2, speed_kmh=40.0),
            rec(2000, 3, speed_kmh=60.0),
        ]))
        self.assertEqual(b["n_events"], 3)
        self.assertEqual(b["n_moving"], 2)
        self.assertAlmostEqual(b["speed_sum"], 100.0)
        self.assertAlmostEqual(b["speed_sumsq"], 40.0 ** 2 + 60.0 ** 2)

    def test_min_max_and_null_skipping(self):
        b = merge_buckets(buckets_for([
            rec(0, 1, engine_temp_c=90.0, odometer_km=10.0),
            rec(1000, 2, engine_temp_c=None, odometer_km=None),
            rec(2000, 3, engine_temp_c=110.0, odometer_km=12.5),
        ]))
        self.assertEqual(b["engine_temp_n"], 2)
        self.assertEqual(b["engine_temp_max"], 110.0)
        self.assertEqual(b["engine_temp_min"], 90.0)
        self.assertEqual(b["engine_temp_high_count"], 1)
        self.assertEqual(b["odo_min"], 10.0)
        self.assertEqual(b["odo_max"], 12.5)
        self.assertNotIn("motor_temp_n", b, "absent signal contributes nothing")
        self.assertNotIn("motor_temp_max", b)

    def test_first_last_order_and_ties(self):
        b = merge_buckets(buckets_for([
            rec(3000, 7, soc_pct=50.0),
            rec(1000, 5, soc_pct=None),
            rec(1000, 6, soc_pct=80.0),
            rec(2000, 9, soc_pct=70.0),
        ]))
        self.assertEqual(b["soc_first"][2], 80.0)
        self.assertEqual(b["soc_last"][2], 50.0)
        # Same timestamp: seq breaks the tie.
        b2 = merge_buckets(buckets_for([rec(1000, 2, soc_pct=20.0), rec(1000, 1, soc_pct=10.0)]))
        self.assertEqual(b2["soc_first"][2], 10.0)
        self.assertEqual(b2["soc_last"][2], 20.0)

    def test_merge_is_order_independent(self):
        rng = random.Random(3)
        recs = [rec(i * 60000, i, speed_kmh=rng.uniform(0, 100), engine_temp_c=rng.choice([None, rng.uniform(80, 115)]),
                    soc_pct=rng.choice([None, rng.uniform(5, 100)]), current_a=rng.uniform(-150, 200),
                    acceleration_ms2=rng.uniform(-3, 2), harsh_brake=rng.random() < 0.1,
                    dtc_codes=rng.choice([[], ["P0301"], ["BRAKE_WEAR_HIGH", "BATT_TEMP_HIGH"]]))
                for i in range(200)]
        bs = buckets_for(recs)
        whole = merge_buckets(bs)
        shuffled = bs[:]
        rng.shuffle(shuffled)
        halves = merge_buckets([merge_buckets(shuffled[:77]), merge_buckets(shuffled[77:])])
        for k, v in whole.items():
            if isinstance(v, float):
                self.assertAlmostEqual(v, halves[k], places=6, msg=k)
            else:
                self.assertEqual(v, halves[k], k)

    def test_dtc_component_counts_and_thresholds(self):
        b = merge_buckets(buckets_for([
            rec(0, 1, dtc_codes=["BRAKE_WEAR_HIGH"], harsh_brake=True, speed_kmh=85.0, acceleration_ms2=-2.0),
            rec(1, 2, dtc_codes=["P0301", "BATT_TEMP_HIGH"], current_a=-120.0, soc_pct=5.0),
            rec(2, 3, current_a=-10.0, acceleration_ms2=-0.5),
        ]))
        self.assertEqual(b["dtc_event_count"], 2)
        self.assertEqual(b["dtc_brake_count"], 1)
        self.assertEqual(b["dtc_powertrain_count"], 1)
        self.assertEqual(b["dtc_battery_count"], 1)
        self.assertEqual(b["harsh_count"], 1)
        self.assertEqual(b["harsh_high_speed_count"], 1)
        self.assertEqual(b["brake_n"], 1)
        self.assertEqual(b["charging_count"], 2)
        self.assertEqual(b["fast_charge_count"], 1)
        self.assertEqual(b["deep_discharge_count"], 1)
        self.assertEqual(b["current_absmax"], 120.0)

    def test_float32_quantization(self):
        b = merge_buckets(buckets_for([rec(0, 1, engine_temp_c=92.3)]))
        self.assertEqual(b["engine_temp_max"], float(np.float32(92.3)))

    def test_spec_ops_are_known(self):
        self.assertTrue(set(STAT_SPEC.values()) <= {COUNT, SUM, MIN, MAX, FIRST, LAST})


if __name__ == "__main__":
    unittest.main()
