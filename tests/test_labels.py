"""Label generator versus an independent brute-force labeler (Phase 4 B7)."""

import random
import unittest

import pandas as pd

from fleetpulse_features.labels import label_snapshots

H = pd.Timedelta(hours=1)
D = pd.Timedelta(days=1)
T0 = pd.Timestamp("2026-01-01T00:00:00Z")


def brute_force(snap_rows, events, data_end, horizon=7 * D):
    out = []
    for comp, t in snap_rows:
        ev = sorted([e for e in events if e["vehicle_component_id"] == comp], key=lambda e: (e["occurred_at"], e["event_type"]))
        trig_after = [e for e in ev if e["event_type"] in ("MAINTENANCE_REQUIRED", "FAILURE") and t < e["occurred_at"]]
        nxt = trig_after[0] if trig_after else None
        label = int(nxt is not None and nxt["occurred_at"] <= t + horizon)
        prev = [e for e in ev if e["occurred_at"] <= t]
        in_gap = bool(prev) and prev[-1]["event_type"] in ("MAINTENANCE_REQUIRED", "FAILURE")
        tail = t + horizon > data_end
        out.append((label, nxt["event_id"] if label else None, in_gap, tail))
    return out


def ev(comp, t, typ, eid):
    return {"vehicle_component_id": comp, "occurred_at": t, "event_type": typ, "event_id": eid}


class TestLabels(unittest.TestCase):
    def run_labeler(self, snap_rows, events, data_end):
        snaps = pd.DataFrame(snap_rows, columns=["vehicle_component_id", "feature_ts"])
        evdf = pd.DataFrame(events, columns=["vehicle_component_id", "occurred_at", "event_type", "event_id"])
        return label_snapshots(snaps, evdf, data_end=data_end)

    def test_boundaries(self):
        t = T0 + 10 * D
        events = [ev("c1", t, "MAINTENANCE_REQUIRED", "at_t"), ev("c2", t + 7 * D, "FAILURE", "at_t_plus_7d"),
                  ev("c3", t + 7 * D + pd.Timedelta(milliseconds=1), "FAILURE", "just_after")]
        lab = self.run_labeler([("c1", t), ("c2", t), ("c3", t)], events, data_end=T0 + 90 * D)
        self.assertEqual(lab["label_7d"].tolist()[1:], [1, 0], "event at t+7d is positive, later is not")
        self.assertEqual(lab.iloc[0]["label_7d"], 0, "event exactly at t is not a positive for t")
        self.assertTrue(lab.iloc[0]["in_gap"], "event at t makes the component in-gap at t")
        self.assertEqual(lab.iloc[1]["label_next_event_id"], "at_t_plus_7d")
        self.assertAlmostEqual(lab.iloc[1]["label_hours_to_event"], 168.0)

    def test_in_gap_and_service(self):
        e = [ev("c", T0 + 1 * D, "MAINTENANCE_REQUIRED", "m1"), ev("c", T0 + 3 * D, "SERVICE_COMPLETED", "s1")]
        lab = self.run_labeler([("c", T0 + 2 * D), ("c", T0 + 3 * D), ("c", T0 + 4 * D), ("c", T0)], e, T0 + 90 * D)
        self.assertEqual(lab["in_gap"].tolist(), [True, False, False, False])
        self.assertEqual(lab["label_7d"].tolist(), [0, 0, 0, 1])

    def test_tail(self):
        lab = self.run_labeler([("c", T0 + 83 * D), ("c", T0 + 83 * D + H)], [], T0 + 90 * D)
        self.assertEqual(lab["tail"].tolist(), [False, True])

    def test_matches_brute_force_on_random_samples(self):
        rng = random.Random(11)
        comps = [f"c{i}" for i in range(15)]
        events = []
        for c in comps:
            t = T0
            for k in range(rng.randint(0, 4)):
                t = t + pd.Timedelta(hours=rng.randint(1, 600))
                events.append(ev(c, t, rng.choice(["MAINTENANCE_REQUIRED", "FAILURE"]), f"{c}-{k}-a"))
                t = t + pd.Timedelta(hours=rng.randint(24, 72))
                if rng.random() < 0.8:
                    events.append(ev(c, t, "SERVICE_COMPLETED", f"{c}-{k}-s"))
        data_end = T0 + 90 * D
        snap_rows = [(rng.choice(comps), T0 + pd.Timedelta(hours=rng.randint(0, 90 * 24))) for _ in range(3000)]
        # force exact-boundary cases
        for e in events[:10]:
            snap_rows += [(e["vehicle_component_id"], e["occurred_at"]), (e["vehicle_component_id"], e["occurred_at"] - 7 * D)]
        lab = self.run_labeler(snap_rows, events, data_end)
        expected = brute_force(snap_rows, events, data_end)
        got = list(zip(lab["label_7d"].tolist(), lab["label_next_event_id"].tolist(), lab["in_gap"].tolist(), lab["tail"].tolist()))
        self.assertEqual(got, expected)
        self.assertGreater(sum(x[0] for x in expected), 50)


if __name__ == "__main__":
    unittest.main()
