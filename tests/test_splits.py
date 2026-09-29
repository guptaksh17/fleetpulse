"""Chronological split boundaries, buffers and the vehicle holdout (Phase 4 B7)."""

import unittest

import numpy as np
import pandas as pd
import yaml

from fleetpulse_features.dataset import build_dataset, model_input_columns, split_ranges
from fleetpulse_features.features import feature_names

START = pd.Timestamp("2026-01-01T00:00:00Z")
END = START + pd.Timedelta(days=90)
with open("configs/dataset.yaml") as f:
    DCFG = yaml.safe_load(f)


def synthetic(n_vehicles=30):
    hours = pd.date_range(START + pd.Timedelta(hours=1), END - pd.Timedelta(hours=1), freq="h")
    rows, comps, events = [], [], []
    rng = np.random.default_rng(1)
    for i in range(n_vehicles):
        vid = f"00000000-0000-0000-0000-{i:012d}"
        vcid = f"vc-{i}"
        comps.append({"vehicle_id": vid, "component": "BRAKE", "vehicle_component_id": vcid, "vehicle_type": "ICE"})
        occ = START + pd.Timedelta(hours=int(rng.integers(24, 88 * 24)))
        events += [{"vehicle_component_id": vcid, "event_id": f"e{i}", "event_type": "MAINTENANCE_REQUIRED", "occurred_at": occ},
                   {"vehicle_component_id": vcid, "event_id": f"s{i}", "event_type": "SERVICE_COMPLETED", "occurred_at": occ + pd.Timedelta(hours=48)}]
        for t in hours:
            rows.append({"vehicle_id": vid, "component": "BRAKE", "feature_ts": t, "window_complete": t >= START + pd.Timedelta(days=1),
                         **{n: 1.0 for n in feature_names("BRAKE")}})
    return pd.DataFrame(rows), pd.DataFrame(comps), pd.DataFrame(events)


class TestSplits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f, c, e = synthetic()
        cls.ds, cls.info = build_dataset(f, c, e, START, END, DCFG["splits"], DCFG["seed"], DCFG["holdout_fraction"], DCFG["horizon_days"])

    def test_rows_only_inside_configured_ranges(self):
        ranges = split_ranges(START, DCFG["splits"])
        for name, (lo, hi) in ranges.items():
            part = self.ds[self.ds["meta_split"] == name]
            self.assertGreater(len(part), 0, name)
            self.assertTrue((part["meta_feature_ts"] >= lo).all() and (part["meta_feature_ts"] < hi).all(), name)
        self.assertEqual(ranges["train"], (START, START + pd.Timedelta(days=52)))
        self.assertEqual(ranges["test"], (START + pd.Timedelta(days=75), START + pd.Timedelta(days=83)))

    def test_buffers_are_empty(self):
        day = ((self.ds["meta_feature_ts"] - START) // pd.Timedelta(days=1)) + 1
        for lo, hi in ((53, 59), (69, 75), (84, 90)):
            self.assertEqual(int(((day >= lo) & (day <= hi)).sum()), 0, f"buffer days {lo}-{hi} must be empty")
        self.assertGreater(self.info["excluded_outside_splits"], 0)

    def test_exclusions_applied(self):
        self.assertGreater(self.info["excluded_warmup"], 0)
        self.assertGreater(self.info["excluded_in_gap"], 0)
        self.assertGreater(self.info["excluded_tail"], 0)
        self.assertTrue((self.ds["meta_feature_ts"] + pd.Timedelta(days=7) <= END).all(), "no kept row has an unobserved horizon")
        self.assertTrue((self.ds["meta_feature_ts"] >= START + pd.Timedelta(days=1)).all(), "warm-up rows excluded")

    def test_holdout_is_deterministic_and_about_20_percent(self):
        f, c, e = synthetic(200)
        ds1, _ = build_dataset(f, c, e, START, END, DCFG["splits"], DCFG["seed"], DCFG["holdout_fraction"], DCFG["horizon_days"])
        groups = ds1.groupby("meta_vehicle_id")["meta_vehicle_group"].first()
        frac = (groups == "holdout").mean()
        self.assertTrue(0.12 <= frac <= 0.28, frac)
        self.assertEqual(groups.nunique(), 2)
        ds2, _ = build_dataset(f, c, e, START, END, DCFG["splits"], DCFG["seed"], DCFG["holdout_fraction"], DCFG["horizon_days"])
        self.assertTrue((ds1["meta_vehicle_group"] == ds2["meta_vehicle_group"]).all())

    def test_only_f_columns_are_model_inputs(self):
        cols = model_input_columns(self.ds, "BRAKE")
        self.assertTrue(cols and all(c.startswith("f_") for c in cols))
        self.assertFalse(any(c.startswith(("meta_", "label_")) for c in cols))


if __name__ == "__main__":
    unittest.main()
