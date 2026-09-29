"""
Streaming feature engine against a real Redis (Phase 4 B4):
  - applying the same event twice changes state once
  - two different events update every statistic correctly
  - TTLs are set on dedup and state keys
  - snapshot catch-up across several hour boundaries emits every snapshot in order
  - crash after the Lua step but before the snapshot write, then replay, still yields the snapshot
Skipped (with the reason printed) when Redis is not reachable on REDIS_HOST:REDIS_PORT.
"""

from dataclasses import replace
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "stream-processor"))

from fleetpulse_features.config import load_feature_config
from fleetpulse_features.stats import columns_from_records, extract_stats, merge_buckets, single_event_bucket

try:
    import redis
    _r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
    _r.ping()
    REDIS_ERROR = None
except Exception as e:  # noqa: BLE001
    REDIS_ERROR = f"Redis not reachable: {e}"

if REDIS_ERROR is None:
    from stream_processor.features.engine import FeatureEngine
    from stream_processor.pipeline import process_batch
    from stream_processor.dedup.stage import DedupStage

H = 3_600_000
T0 = 1_767_225_600_000  # 2026-01-01T00:00:00Z
VID = "11111111-2222-3333-4444-555555555555"
FCFG = replace(load_feature_config(), redis_key_prefix="fstest")


class Ctx:
    def get(self, vid):
        return {"vehicle_type": "EV", "manufacture_date_ms": T0 - 365 * 24 * H, "components": ["BRAKE", "BATTERY"], "services": {}}


def rec(ts, seq, **kw):
    r = {"vehicle_id": VID, "event_ts_ms": ts, "seq": seq, "speed_kmh": 40.0, "odometer_km": 100.0 + seq,
         "acceleration_ms2": -1.5, "harsh_brake": False, "dtc_codes": [], "soc_pct": 80.0 - seq, "voltage_v": 390.0,
         "current_a": 20.0 + seq, "temperature_c": 30.0, "motor_temp_c": 60.0, "engine_temp_c": None,
         "power_kw": 25.0, "engine_load_pct": None, "rpm": None}
    r.update(kw)
    return r


@unittest.skipIf(REDIS_ERROR is not None, REDIS_ERROR or "")
class TestFeatureLua(unittest.TestCase):
    def setUp(self):
        self.r = _r
        self._clean()
        self.rows = []
        self.engine = FeatureEngine(self.r, FCFG, Ctx(), self.rows.extend, dedup_ttl_seconds=900, dedup_prefix="deduptest")

    def tearDown(self):
        self._clean()

    def _clean(self):
        for pattern in ("fstest:*", "deduptest:*"):
            keys = list(_r.scan_iter(match=pattern))
            if keys:
                _r.delete(*keys)

    def bucket(self, width):
        return self.engine._load_buckets(VID)[width]

    def test_same_event_twice_changes_state_once(self):
        e = rec(T0 + 1000, 1)
        self.assertEqual(self.engine.apply_records([e])["applied"], 1)
        before = self.r.hgetall(f"fstest:{VID}:b60")
        res = self.engine.apply_records([e])
        self.assertEqual(res, {"applied": 0, "duplicates_skipped": 1, "late_ignored": 0})
        self.assertEqual(self.r.hgetall(f"fstest:{VID}:b60"), before)
        self.assertEqual(self.r.hget(f"fstest:{VID}:meta", "events_applied"), "1")

    def test_two_events_update_all_statistics(self):
        e1, e2 = rec(T0 + 1000, 1), rec(T0 + 2000, 2, harsh_brake=True, soc_pct=None, dtc_codes=["BATT_TEMP_HIGH"])
        self.engine.apply_records([e1, e2])
        st = extract_stats(columns_from_records([e1, e2]), FCFG.thresholds)
        expected = merge_buckets([single_event_bucket(st, 0, e1["event_ts_ms"], 1), single_event_bucket(st, 1, e2["event_ts_ms"], 2)])
        for width in (60, 900):
            got = self.bucket(width)
            self.assertEqual(list(got), [(T0 + 1000) // (width * 1000)])
            b = got[(T0 + 1000) // (width * 1000)]
            self.assertEqual(set(b), set(expected))
            for k, v in expected.items():
                if isinstance(v, float):
                    self.assertAlmostEqual(b[k], v, places=9, msg=k)
                else:
                    self.assertEqual(b[k], v, k)
        self.assertEqual(b["soc_first"][2], e1["soc_pct"])
        self.assertEqual(b["soc_last"][2], e1["soc_pct"], "null soc on the later event is skipped")

    def test_ttls_are_set(self):
        self.engine.apply_records([rec(T0 + 1000, 1)])
        self.assertTrue(0 < self.r.ttl(f"deduptest:{VID}:1") <= 900)
        for k in (f"fstest:{VID}:b60", f"fstest:{VID}:b900", f"fstest:{VID}:i60", f"fstest:{VID}:meta"):
            self.assertTrue(0 < self.r.ttl(k) <= FCFG.state_ttl_seconds, k)

    def test_catch_up_emits_every_snapshot_in_order(self):
        self.engine.apply_records([rec(T0 + 10 * 60000, 1)])
        self.assertEqual(self.engine.snapshot_step([VID]), 0, "no boundary crossed yet")
        self.engine.apply_records([rec(T0 + 4 * H + 5000, 2)])  # crosses 01:00, 02:00, 03:00, 04:00
        n = self.engine.snapshot_step([VID])
        self.assertEqual(n, 4 * 2)
        ts = [r[2] for r in self.rows if r[1] == "BRAKE"]
        self.assertEqual(ts, [T0 + k * H for k in (1, 2, 3, 4)])
        by_t = {r[2]: r[5] for r in self.rows if r[1] == "BRAKE"}
        self.assertEqual(by_t[T0 + H]["w1h_events"], 1.0)
        self.assertEqual(by_t[T0 + 2 * H]["w1h_events"], 0.0)
        self.assertEqual(by_t[T0 + 4 * H]["w24h_events"], 1.0, "event at t=04:00:05 is not in the window of t=04:00")
        self.assertEqual(int(self.r.hget(f"fstest:{VID}:meta", "last_snapshot_ts")), T0 + 4 * H)
        self.assertEqual(self.engine.snapshot_step([VID]), 0, "idempotent: nothing new is due")

    def test_crash_between_lua_and_snapshot_then_replay(self):
        stage = DedupStage(self.r, window_seconds=900, bloom_capacity=1000, key_prefix="deduptest")
        b1 = [rec(T0 + 60000, 1)]
        process_batch(b1, stage, lambda evs: None, feature_engine=self.engine)
        b2 = [rec(T0 + H + 60000, 2)]
        # Crash: Lua applied, snapshot step and offset commit never ran.
        self.engine.apply_records(b2)
        self.assertEqual(self.rows, [])
        # Restart with fresh objects; Kafka redelivers the uncommitted batch.
        rows2 = []
        engine2 = FeatureEngine(self.r, FCFG, Ctx(), rows2.extend, dedup_ttl_seconds=900, dedup_prefix="deduptest")
        stage2 = DedupStage(self.r, window_seconds=900, bloom_capacity=1000, key_prefix="deduptest")
        res = process_batch(b2, stage2, lambda evs: None, feature_engine=engine2)
        self.assertEqual(res["accepted"], 0, "redelivered event is a duplicate")
        self.assertEqual([r[2] for r in rows2 if r[1] == "BRAKE"], [T0 + H])
        self.assertEqual(rows2[0][5]["w1h_events"], 1.0, "no double counting after replay")

    def test_late_event_is_counted_and_ignored(self):
        self.engine.apply_records([rec(T0 + 30 * H, 2)])
        res = self.engine.apply_records([rec(T0 + 60000, 1)])
        self.assertEqual(res["late_ignored"], 1)
        self.assertEqual(self.r.exists(f"deduptest:{VID}:1"), 1, "late event still marked as seen")


if __name__ == "__main__":
    unittest.main()
