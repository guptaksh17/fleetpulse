#!/usr/bin/env python3
"""
Resume equivalence check (Phase 3 Part A3).

Run A: N days continuously.
Run B: N/2 days, checkpoint to a JSON file on disk, restore into a fresh engine, N/2 more days.
Both runs serialize every telemetry payload and every maintenance event as sorted-key JSON
lines. The check passes only if the two streams are byte-identical (same SHA-256).
"""

import argparse
from datetime import timedelta
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.config import load_config
from simulator.engine import FleetEngine


class StreamHasher:
    def __init__(self):
        self.h = hashlib.sha256()
        self.lines = 0
        self.events = 0

    def on_step(self, sim_time, outputs):
        for out in outputs:
            self.h.update(json.dumps({"vehicle_id": out.vehicle.vehicle_id, **out.payload}, sort_keys=True).encode())
            self.h.update(b"\n")
            self.lines += 1
            for e in out.events:
                self.h.update(json.dumps(e.to_dict(), sort_keys=True).encode())
                self.h.update(b"\n")
                self.events += 1

    def digest(self):
        return self.h.hexdigest()


def run(config_path: str, vehicles: int, days: int, checkpoint_path: str) -> dict:
    half = days // 2

    cfg = load_config(config_path)
    cfg.population.vehicles = vehicles
    a = FleetEngine(cfg)
    ha = StreamHasher()
    a.run(a.start_time + timedelta(days=days), ha.on_step)

    cfg_b = load_config(config_path)
    cfg_b.population.vehicles = vehicles
    b1 = FleetEngine(cfg_b)
    hb = StreamHasher()
    b1.run(b1.start_time + timedelta(days=half), hb.on_step)
    b1.save_checkpoint(checkpoint_path)
    del b1

    cfg_b2 = load_config(config_path)
    cfg_b2.population.vehicles = vehicles
    b2 = FleetEngine(cfg_b2)
    b2.load_checkpoint(checkpoint_path)
    b2.run(b2.start_time + timedelta(days=days), hb.on_step)

    return {
        "continuous": {"sha256": ha.digest(), "telemetry_lines": ha.lines, "events": ha.events},
        "resumed": {"sha256": hb.digest(), "telemetry_lines": hb.lines, "events": hb.events},
        "identical": ha.digest() == hb.digest(),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/offline_train.yaml")
    p.add_argument("--vehicles", type=int, default=50)
    p.add_argument("--days", type=int, default=10)
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as d:
        res = run(args.config, args.vehicles, args.days, os.path.join(d, "checkpoint.json"))
    print(json.dumps(res, indent=2))
    print("RESUME EQUIVALENCE:", "PASS" if res["identical"] else "FAIL")
    sys.exit(0 if res["identical"] else 1)


if __name__ == "__main__":
    main()
