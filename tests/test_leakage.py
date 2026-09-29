"""
Leakage tests (Phase 4): features at t use only telemetry with event_ts < t and service
records with occurred_at <= t.
"""

from dataclasses import replace
import os
import random
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "stream-processor"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fleetpulse_features.config import load_feature_config
from stream_processor.features.context_cache import StaticContextProvider
from build_features_offline import compute_features_frame
from fixtures_sim import simulate_frame, same_features

H = 3_600_000
FCFG = load_feature_config()
SIGNAL_COLS = ["speed_kmh", "odometer_km", "acceleration_ms2", "soc_pct", "voltage_v", "current_a",
               "temperature_c", "motor_temp_c", "engine_temp_c", "power_kw", "engine_load_pct", "rpm"]


def by_key(rows):
    return {(r["vehicle_id"], r["component"], r["feature_ts_ms"]): r for r in rows}


class TestOfflineLeakage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df, cls.ctx, _ = simulate_frame(vehicles=4, days=3.0)
        cls.provider = StaticContextProvider(cls.ctx)

    def test_future_telemetry_deleted_or_perturbed_does_not_change_features(self):
        rng = random.Random(5)
        vids = sorted(self.df["vehicle_id"].unique())
        start = int(self.df["event_ts_ms"].min())
        checked = 0
        for _ in range(12):
            vid = rng.choice(vids)
            t = start + rng.randint(26, 70) * H
            full = by_key(compute_features_frame(self.df, self.provider, FCFG, {vid: [t]}))
            deleted = by_key(compute_features_frame(self.df[self.df["event_ts_ms"] < t], self.provider, FCFG, {vid: [t]}))
            pert = self.df.copy()
            future = pert["event_ts_ms"] >= t
            nrng = np.random.default_rng(t)
            for c in SIGNAL_COLS:
                pert.loc[future, c] = nrng.uniform(-500, 500, int(future.sum()))
            pert.loc[future, "harsh_brake"] = True
            pert.loc[future, "dtc_codes"] = pert.loc[future, "dtc_codes"].apply(lambda _: ["BRAKE_SYSTEM_CRITICAL", "P0301"])
            perturbed = by_key(compute_features_frame(pert, self.provider, FCFG, {vid: [t]}))
            self.assertTrue(full)
            self.assertEqual(full.keys(), deleted.keys())
            self.assertEqual(full.keys(), perturbed.keys())
            for k in full:
                self.assertTrue(same_features(full[k], deleted[k]), f"deleting future events changed {k}")
                self.assertTrue(same_features(full[k], perturbed[k]), f"perturbing future events changed {k}")
                checked += 1
        self.assertGreater(checked, 12)

    def test_service_history_uses_only_occurred_at_le_t(self):
        vid = sorted(self.ctx)[0]
        t = int(self.df["event_ts_ms"].min()) + 30 * H
        base = dict(self.ctx[vid])
        comps = base["components"]
        no_svc = StaticContextProvider({vid: {**base, "services": {}}})
        future_svc = StaticContextProvider({vid: {**base, "services": {c: [(t + 1, 1.0), (t + H, 2.0)] for c in comps}}})
        at_t = StaticContextProvider({vid: {**base, "services": {c: [(t, 5.0), (t + H, 9.0)] for c in comps}}})
        a = by_key(compute_features_frame(self.df, no_svc, FCFG, {vid: [t]}))
        b = by_key(compute_features_frame(self.df, future_svc, FCFG, {vid: [t]}))
        c = by_key(compute_features_frame(self.df, at_t, FCFG, {vid: [t]}))
        for k in a:
            self.assertTrue(same_features(a[k], b[k]), "a service after t leaked into features at t")
            self.assertEqual(c[k]["days_since_service"], 0.0, "a service exactly at t is known at t")
            self.assertEqual(c[k]["has_service_history"], 1.0)
            self.assertAlmostEqual(c[k]["distance_since_service_km"], c[k]["odometer_km"] - 5.0)


try:
    import redis
    _r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
    _r.ping()
    REDIS_ERROR = None
except Exception as e:  # noqa: BLE001
    REDIS_ERROR = f"Redis not reachable: {e}"


@unittest.skipIf(REDIS_ERROR is not None, REDIS_ERROR or "")
class TestStreamingLeakage(unittest.TestCase):
    def run_stream(self, records, provider):
        from stream_processor.features.engine import FeatureEngine
        from warm_feature_state import records_from_frame

        for pattern in ("fsleak:*", "dedupleak:*"):
            keys = list(_r.scan_iter(match=pattern))
            if keys:
                _r.delete(*keys)
        rows = []
        eng = FeatureEngine(_r, replace(FCFG, redis_key_prefix="fsleak"), provider, rows.extend, dedup_prefix="dedupleak")
        recs = list(records_from_frame(records))
        for i in range(0, len(recs), 300):
            eng.apply_records(recs[i: i + 300])
            eng.snapshot_step({r["vehicle_id"] for r in recs[i: i + 300]})
        return {(r[0], r[1], r[2]): r[5] for r in rows}

    def test_streaming_snapshot_ignores_future_values(self):
        df, ctx, _ = simulate_frame(vehicles=1, days=2.0, seed=9)
        df = df.sort_values(["event_ts_ms", "seq"])
        provider = StaticContextProvider(ctx)
        t = int(df["event_ts_ms"].min()) + 30 * H
        full = self.run_stream(df, provider)
        pert = df.copy()
        future = pert["event_ts_ms"] >= t
        for c in SIGNAL_COLS:
            pert.loc[future, c] = 999.0
        perturbed = self.run_stream(pert, provider)
        keys = [k for k in full if k[2] <= t]
        self.assertTrue(any(k[2] == t for k in keys))
        for k in keys:
            self.assertTrue(same_features(full[k], perturbed[k]), f"future values changed snapshot {k}")
        for pattern in ("fsleak:*", "dedupleak:*"):
            keys = list(_r.scan_iter(match=pattern))
            if keys:
                _r.delete(*keys)


if __name__ == "__main__":
    unittest.main()
