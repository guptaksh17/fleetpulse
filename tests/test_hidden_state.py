"""
Hidden-state scan (Phase 4): no health, wear, lifecycle or driver-profile value can reach a
feature input. Checks the extractor's declared inputs, statistic and feature names, the
context SQL, and that injecting hidden fields into an input batch changes nothing.
"""

import os
import re
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "stream-processor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fleetpulse_features.config import load_feature_config
from fleetpulse_features.features import feature_names
from fleetpulse_features.stats import INPUT_COLUMNS, STAT_SPEC, columns_from_records, extract_stats
from stream_processor.features import context_cache
from stream_processor.features.engine import flatten_canonical_event

FORBIDDEN = re.compile(r"health|wear|lifecycle|stress|pending|degradation|profile|behavio|driver|failure_type|recovery")


def telemetry_columns():
    with open(os.path.join(ROOT, "db", "timescale", "init.sql")) as f:
        sql = f.read()
    body = sql[sql.index("CREATE TABLE IF NOT EXISTS telemetry"):]
    body = body[: body.index(");")]
    return {line.strip().split()[0] for line in body.splitlines()[1:] if line.strip()}


class TestHiddenState(unittest.TestCase):
    def test_extractor_reads_only_telemetry_columns(self):
        cols = telemetry_columns()
        for c in INPUT_COLUMNS:
            if c == "event_ts_ms":
                continue  # derived from event_ts
            self.assertIn(c, cols, f"extractor input {c} is not a telemetry column")
            self.assertIsNone(FORBIDDEN.search(c), c)

    def test_names_carry_no_hidden_state(self):
        for stat in STAT_SPEC:
            self.assertIsNone(FORBIDDEN.search(stat), stat)
        for comp in ("BRAKE", "POWERTRAIN", "BATTERY"):
            for n in feature_names(comp):
                self.assertIsNone(FORBIDDEN.search(n), n)

    def test_context_sql_reads_no_hidden_metadata(self):
        for sql in (context_cache.VEHICLES_SQL, context_cache.SERVICES_SQL):
            self.assertNotIn("metadata", sql, "maintenance_event.metadata holds hidden health/wear and must not be read")
            self.assertIsNone(FORBIDDEN.search(sql.lower()))

    def test_hidden_fields_in_input_have_no_effect(self):
        from fixtures_sim import simulate_frame

        df, _, eng = simulate_frame(vehicles=2, days=0.5)
        recs = df.head(400).to_dict("records")
        fcfg = load_feature_config()
        clean = extract_stats(columns_from_records(recs), fcfg.thresholds)
        dirty_recs = [{**r, "health": 1.0, "wear": 99.0, "lifecycle": "FAILURE", "driving_profile": "CITY"} for r in recs]
        dirty = extract_stats(columns_from_records(dirty_recs), fcfg.thresholds)
        for k in clean:
            np.testing.assert_array_equal(clean[k], dirty[k])
        # And the simulator really does hold hidden state that the payload never exposes.
        v = eng.vehicles[0]
        self.assertTrue(hasattr(v.components["BRAKE"], "wear"))

    def test_canonical_flattening_drops_everything_else(self):
        event = {"vehicle_id": "v", "seq": 1, "event_ts": "2026-01-01T00:00:00.000Z", "speed_kmh": 1.0,
                 "battery": {"soc_pct": 50.0, "health": 3.0}, "powertrain": {"wear": 7.0}, "lifecycle": "AT_RISK"}
        flat = flatten_canonical_event(event)
        self.assertFalse(any(FORBIDDEN.search(k) for k in flat))


if __name__ == "__main__":
    unittest.main()
