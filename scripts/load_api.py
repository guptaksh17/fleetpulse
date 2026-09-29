#!/usr/bin/env python3
"""
API load test: N concurrent authenticated clients for D seconds over the main read endpoints.
Reports requests/s, error rate and p50 / p95 / p99 latency per endpoint and overall.
The rate limit is raised for the load-test principal by running the API with a high
RATE_LIMIT_PER_MINUTE; this measures latency, not the limiter.
"""

import argparse
import json
import os
import random
import threading
import time
import urllib.request

ENDPOINTS = ["/api/v1/summary", "/api/v1/priority?limit=25", "/api/v1/alerts?limit=25", "/api/v1/vehicles?limit=50", "VEHICLE"]


def req(url, token=None, data=None):
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                               headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return resp.status, json.loads(resp.read() or b"null")


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", default="admin@fleetpulse.local")
    ap.add_argument("--password", default=os.getenv("FP_ADMIN_PASSWORD"))
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=60)
    args = ap.parse_args()

    _, body = req(args.base + "/api/v1/auth/login", data={"email": args.email, "password": args.password})
    token = body["access_token"]
    _, veh = req(args.base + "/api/v1/vehicles?limit=200", token)
    vids = [v["vehicle_id"] for v in veh["items"]]
    lat = {e: [] for e in ENDPOINTS}
    errors = {e: 0 for e in ENDPOINTS}
    stop = time.time() + args.seconds
    lock = threading.Lock()

    def worker(seed):
        rng = random.Random(seed)
        while time.time() < stop:
            e = rng.choice(ENDPOINTS)
            path = f"/api/v1/vehicles/{rng.choice(vids)}" if e == "VEHICLE" else e
            t0 = time.perf_counter()
            try:
                status, _ = req(args.base + path, token)
                ok = status == 200
            except Exception:
                ok = False
            ms = (time.perf_counter() - t0) * 1000
            with lock:
                (lat[e].append(ms) if ok else None)
                if not ok:
                    errors[e] += 1

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(args.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0
    allv = [x for v in lat.values() for x in v]
    report = {
        "concurrency": args.concurrency, "seconds": round(elapsed, 1), "requests": len(allv) + sum(errors.values()),
        "requests_per_second": round((len(allv) + sum(errors.values())) / elapsed, 1),
        "errors": sum(errors.values()),
        "overall_ms": {"p50": pct(allv, 0.5), "p95": pct(allv, 0.95), "p99": pct(allv, 0.99)},
        "per_endpoint_ms": {e: {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95), "p99": pct(v, 0.99), "errors": errors[e]} for e, v in lat.items()},
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
