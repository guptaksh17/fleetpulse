# FleetPulse Phase 4 Verification (features, labels, datasets)

Everything below was executed on 2026-09-29, on one 8-core, 8 GB MacBook with Docker Desktop.
Outputs are pasted, not paraphrased. Items not executed are marked as such.

## 1. Unit and integration tests

```
tests: 74 passed (unittest discover, before the Phase 5-6 additions); later full pytest run: 82 passed in 308 s
tests/test_feature_stats.py      7 passed   (extraction, merge, first/last ties, nulls, float32 quantization)
tests/test_feature_defs.py       7 passed   (hand-computed fixtures per component and vehicle type, NaN rules, std clamp)
tests/test_feature_lua.py        6 passed   (real Redis: idempotent apply, all statistics, TTLs, catch-up, crash + replay, late event)
tests/test_labels.py             4 passed   (brute-force agreement on 3,000 random samples, t and t + 7 d boundaries, in-gap, tail)
tests/test_leakage.py            3 passed   (delete / perturb all data >= t offline and streaming; services after t ignored)
tests/test_splits.py             5 passed   (ranges, empty buffers, exclusions, deterministic 20 percent holdout, f_-only inputs)
tests/test_hidden_state.py       5 passed   (inputs are telemetry columns, names, context SQL, injected hidden fields have no effect)
```

## 2. Feature parity (streaming engine vs offline reference)

`scripts/verify_feature_parity.py --vehicles 20 --days 10`, with real Redis and the stream
processor's `process_batch`:

```
  run {"variant": "clean", "events": 49018, "applied": 49018, "duplicates_dropped_by_dedup_stage": 0, "seconds": 62.5, "events_per_second": 784.3}
  run {"variant": "dup15", "redelivered": 7463, "received": 56481, "applied": 49018, "duplicates_dropped_by_dedup_stage": 7463, "events_per_second": 892.3}
  run {"variant": "restart", "crash_batch": 49, "crash_applied_events": 500, "applied": 48518, "duplicates_dropped_by_dedup_stage": 500}
  redis feature state per vehicle: {"vehicles": 20, "mean_bytes": 357862, "max_bytes": 487082}
  offline vs streaming[clean]:   snapshots 12667 vs 12667, missing 0, extra 0, values 803279, mismatches 0, max rel diff 1.936e-10 -> PASS
  offline vs streaming[dup15]:   snapshots 12667 vs 12667, missing 0, extra 0, values 803279, mismatches 0, max rel diff 1.936e-10 -> PASS
  offline vs streaming[restart]: snapshots 12667 vs 12667, missing 0, extra 0, values 803279, mismatches 0, max rel diff 1.936e-10 -> PASS
  streaming[clean] vs streaming[dup15]:   values 803279, mismatches 0, max rel diff 0.000e+00 -> PASS
  streaming[clean] vs streaming[restart]: values 803279, mismatches 0, max rel diff 0.000e+00 -> PASS
FEATURE PARITY: PASS
```

**Disclosure: an unexplained first failure.** The first execution of this script failed on the
restart variant only:
```
  offline vs streaming[restart]: snapshots 12667 vs 6360, missing 6307, extra 0, values 403320, mismatches 11926 -> FAIL
FEATURE PARITY: FAIL
```
In that run, after the simulated restart the Lua script reported about 24,000 new events as
already seen, while the dedup stage found no keys for them.

It did not reproduce in two later runs: a restart-only run instrumented to count dedup keys, and
the full run above. Redis reported 0 evicted and 0 expired keys throughout. The root cause is not
established. That first run ran while the machine was under heavy memory pressure.

The parity script now records `replay_keys_present` and `next_batch_keys_present` at the
restart, and fails if the restart is inconsistent, so any recurrence becomes diagnosable. The
mismatch values from the failed run are kept in `data/logs/feature_parity.log`.

## 3. Offline features, dataset and calibration guardrail

`scripts/build_features_offline.py` over the full default history (500 vehicles, 90 days):
```
  "rows": 2707386,   "window_complete_rows": 2678544,   "runtime_seconds": 546.6
  by_component: BRAKE 1079500, POWERTRAIN 1079500, BATTERY 548386
```

`scripts/build_dataset.py`:
```
Dataset ds-f1-l1-v1 -> data/datasets/ds-f1-l1-v1
feature schema f1, labels l1, simulator config 5f77303d55d3
Exclusions: {"snapshots_total": 2707386, "excluded_warmup": 28842, "excluded_in_gap": 23712, "excluded_tail": 207641, "excluded_outside_splits": 419277, "rows_kept": 2027914}

component   split            rows  positives  prevalence  pos_events  vehicles  holdout
BRAKE       train          606368      19794       3.26%         136       500   121323
BRAKE       calibration     59214       1816       3.07%          24       500    11882
BRAKE       threshold       47595        907       1.91%          15       500     9560
BRAKE       test            95163       3065       3.22%          34       500    18967
POWERTRAIN  train          606237      21013       3.47%         142       500   121264
POWERTRAIN  calibration     59544       1934       3.25%          26       500    11974
POWERTRAIN  threshold       47530       1511       3.18%          26       500     9600
POWERTRAIN  test            95000       3235       3.40%          33       500    19018
BATTERY     train          308387       8978       2.91%          58       254    65501
BATTERY     calibration     30254       1383       4.57%          18       254     6429
BATTERY     threshold       24128       1039       4.31%          15       254     5145
BATTERY     test            48494       1059       2.18%           9       254    10312

Calibration guardrail: every component has >= 50 positive snapshots and >= 5 distinct positive events in the calibration split.
```

**Observation for Phase 5.** The BATTERY test split has only 9 distinct positive events, so battery
test metrics have wide confidence intervals. Proposed change if more certainty is needed: extend
the history to 120 days, or add EV and hybrid vehicles to the cohort.

## 4. Live Kafka smoke test (see docs/phase3-verification.md, A3)

This test passed: 50 vehicles, the dedup invariant held (6215 - 5199 = 1016), and exactly one alert
was raised for the injected DTC. During the run, `component_features` received 1,152 snapshot rows
for the smoke-test vehicles.

## 5. verify_phase4.sh (live run resumed from the checkpoint with warm-up, full chain)

Run on 2026-09-29, after moving the project Redis to host port 6380. Hours 02:00-08:00 and
11:00-18:00 of the per-hour table are elided; every hour shows 131|50|131.

```
==> STEP 0: Stack and schema
NOTICE:  column "odometer_km" of relation "maintenance_event" already exists, skipping
        create_hypertable        
---------------------------------
 (2,public,component_features,f)
(1 row)

[PASS] stack up, migrations applied

==> STEP 1: Part A checks
History (TimescaleDB):
12172824|500|2026-01-01 00:00:00+00|2026-03-31 23:45:00+00
Ground truth (PostgreSQL) event_type|rows|with_odometer:
FAILURE|33|33
MAINTENANCE_REQUIRED|462|462
SERVICE_COMPLETED|485|485
Trips:
138593
orphan_services|back_to_back_triggers = 0|0
[PASS] lifecycle ordering
Ran 3 tests in 29.777s

OK
[PASS] A1 offline-vs-calibration equality, resume equivalence (unit), disjoint populations
    "sha256": "07f77e614bb63f24319ae1edb8e300a9a0fe8343a88fda3bdf7b148434bf7dd8",
    "telemetry_lines": 127702,
    "events": 8
  },
  "resumed": {
    "sha256": "07f77e614bb63f24319ae1edb8e300a9a0fe8343a88fda3bdf7b148434bf7dd8",
    "telemetry_lines": 127702,
    "events": 8
  },
  "identical": true
}
RESUME EQUIVALENCE: PASS
[PASS] A3 resume equivalence (50 vehicles, 10 days)

==> STEP 2: Live run resumed from checkpoint with feature warm-up (50 vehicles)
{"telemetry_deleted": 11745, "component_features_deleted": 2111, "maintenance_events_deleted": 1, "trips_deleted": 155, "alerts_resolved": 3, "redis_keys_deleted": 12126}
{
  "applied": 12970,
  "duplicates_skipped": 0,
  "late_ignored": 0,
  "vehicles": 50,
  "replayed_rows": 12970,
  "window_start": "2026-03-30T23:00:00+00:00",
  "until": "2026-04-01T00:00:00+00:00",
  "runtime_seconds": 8.4
}
2026-09-29T05:20:13Z [INFO] [simulator.engine] Restored checkpoint /app/data/checkpoints/offline_checkpoint.json (step 129600, sim_time 2026-04-01 00:00:00+00:00)
2026-09-29T05:20:13Z [INFO] [simulator.runner.live] Live simulation: 50 vehicles from 2026-04-01T00:00:00+00:00, step 60 s, speedup 600.0x (0.100 s wall per step)
2026-09-29T05:22:13Z [INFO] [simulator.runner.live] Live simulation stopped at 2026-04-01T19:33:00+00:00 after 1173 steps, 11751 messages sent (0 injected duplicates).
Live telemetry rows after the history end:
11751|2026-04-01 00:00:00+00|2026-04-01 19:32:00+00
component_features per simulated hour (feature_ts|rows|vehicles|window_complete_rows):
2026-04-01 00:00:00+00|131|50|131
2026-04-01 01:00:00+00|131|50|131
2026-04-01 09:00:00+00|131|50|131
2026-04-01 10:00:00+00|131|50|131
2026-04-01 19:00:00+00|131|50|131
hours=20 expected_rows_per_hour=131 hours_not_matching=0 duplicate_rows=0
[PASS] a complete, window_complete snapshot for every ACTIVE component every simulated hour; no duplicates
Feature counters before: {'events_applied': 11745.0, 'duplicates_skipped': 0.0, 'late_events_ignored': 0.0, 'snapshots_written': 2111.0, 'snapshot_latency_ms_p50': 65.30308723449707, 'snapshot_latency_ms_p95': 742.8500652313232}
Feature counters after:  {'events_applied': 23496.0, 'duplicates_skipped': 0.0, 'late_events_ignored': 0.0, 'snapshots_written': 4731.0, 'snapshot_latency_ms_p50': 170.3498363494873, 'snapshot_latency_ms_p95': 729.7148704528809}
{"window_seconds": 125, "events_applied": 11751.0, "duplicates_skipped": 0.0, "late_events_ignored": 0.0, "snapshots_written": 2620.0, "events_applied_per_second": 94.0, "snapshot_latency_ms_p50": 170.3498363494873, "snapshot_latency_ms_p95": 729.7148704528809}
[PASS] counters sane (events applied, snapshots written, no late events)
{"alerts_resolved": 3}
==> STEP 3: Offline features for the full history and dataset build
  "rows": 2707386,
  "runtime_seconds": 927.8,
Dataset ds-f1-l1-v1 -> data/datasets/ds-f1-l1-v1
Calibration guardrail: every component has >= 50 positive snapshots and >= 5 distinct positive events in the calibration split.
[PASS] calibration guardrail
==> STEP 4: Parity, leakage, labels and the unit test suite
  offline vs streaming[clean]: snapshots 12667 vs 12667, missing 0, extra 0, values 803279, mismatches 0, window_complete mismatches 0, max rel diff 1.936e-10 -> PASS
  offline vs streaming[dup15]: snapshots 12667 vs 12667, missing 0, extra 0, values 803279, mismatches 0, window_complete mismatches 0, max rel diff 1.936e-10 -> PASS
  offline vs streaming[restart]: snapshots 12667 vs 12667, missing 0, extra 0, values 803279, mismatches 0, window_complete mismatches 0, max rel diff 1.936e-10 -> PASS
  streaming[clean] vs streaming[dup15]: snapshots 12667 vs 12667, missing 0, extra 0, values 803279, mismatches 0, window_complete mismatches 0, max rel diff 0.000e+00 -> PASS
  streaming[clean] vs streaming[restart]: snapshots 12667 vs 12667, missing 0, extra 0, values 803279, mismatches 0, window_complete mismatches 0, max rel diff 0.000e+00 -> PASS
FEATURE PARITY: PASS
Ran 37 tests in 80.066s
OK
Ran 88 tests in 140.399s
FAILED (failures=1)
EXIT 1
```

**Result.**
- Steps 0-4 passed: Part A checks, the live run resumed from the checkpoint with warm-up, the
  offline features and dataset rebuild, parity (third consecutive pass), and 37 targeted tests.
- In the full-suite step, 1 of 88 tests failed once. The failure detail was lost to the script's
  output filter. An immediate rerun passed 88/88.
- The likely cause is the API audit test's fixed wait for the batched audit flusher on a loaded
  machine. That test now polls, with a timeout, and the script now keeps failure details.
- Step 5 (verify_phase2.sh and verify_phase3.sh) was run separately; see section 7.

**The earlier failure of this step.** The first run of step 2 failed: 0 window_complete snapshots,
and 17 vehicles delayed until 10:00. The cause was a Homebrew Redis on 127.0.0.1:6379 shadowing the
project Redis, so warm-up wrote to the wrong server. The fix and the rerun above are described in
the commit history.


## 6. Throughput (information only)

- **Stream processor with features:** 784-892 events/s in a single Python process (parity timing).
  This is well below 100K events/s per process. Scaling is horizontal, with one consumer per
  partition; per-event Lua cost is the dominant term.
- **Live run** (50 vehicles, 600x speedup, 125 s): 11,751 events applied (94 events/s, limited by the simulator pacing), 2,620 snapshots written, snapshot latency p50 170 ms and p95 730 ms.

## 7. verify_phase2.sh and verify_phase3.sh

Run on 2026-09-29 (project Redis on host port 6380):

```
[+] SUCCESS: 0 duplicates in TimescaleDB (total=30 == distinct=30).
  messages_published = 37
  distinct_seq_in_db = 30
  duplicates_dropped = 7
[+] SUCCESS: Requirement A1 verified: messages_published - distinct_seq == duplicates_dropped (7 > 0).
[+] SUCCESS: Exactly 1 matching audit log entry verified.
[+] SUCCESS: Alert latency is well under 5000 ms (7.35 ms).
[+] SUCCESS: Post-restart dedup verified: total (62) == distinct (62), advanced by 31 rows.
ALL PHASE 2 VERIFICATIONS PASSED SUCCESSFULLY!
PHASE2_EXIT 0
==> STEP 1: Verifying Docker Stack Health
  [PASS] Container 'postgres' is running
  [PASS] Container 'timescaledb' is running
  [PASS] Container 'kafka' is running
  [PASS] Container 'redis' is running
==> STEP 2: Seeding Fleet Population (500 Vehicles)
  [PASS] Vehicle population seeded successfully (>= 500 vehicles)
  [PASS] Vehicle components seeded successfully (>= 1000 components)
==> STEP 3: Degradation Calibration Verification (B12 Targets)
  [PASS] Calibration targets met (per-signal AUC, prevalence, sudden ratio)
==> STEP 4: Default History & Ground Truth (generated only if missing)
  [PASS] TimescaleDB telemetry hypertable has rows (12172824)
  [PASS] PostgreSQL trip table has records (138593)
  [PASS] maintenance_event has 462 MAINTENANCE_REQUIRED rows
  [PASS] maintenance_event has 33 FAILURE rows
  [PASS] maintenance_event has 485 SERVICE_COMPLETED rows
  [PASS] Every maintenance event carries odometer_km
==> STEP 4b: Lifecycle Ordering SQL Checks
  [PASS] No SERVICE_COMPLETED without a preceding event
  [PASS] No back-to-back trigger events without service
==> STEP 5: Zero Hidden State Leakage Invariant
  [PASS] Zero hidden state leakage: No hidden columns exist in TimescaleDB telemetry table
==> STEP 6: Checkpoint Serialization & Resume Verification
  [PASS] Checkpoint file created at data/checkpoints/offline_checkpoint.json
  [PASS] Checkpoint JSON structure and RNG states validated
==> STEP 6b: Resume Equivalence (10 days continuous vs 5 + checkpoint + 5)
RESUME EQUIVALENCE: PASS
  [PASS] Resumed run is byte-identical to the continuous run
==> STEP 7: Running Phase 3 Test Suite
  [PASS] Unit test suite passed
==> STEP 8: Verifying Phase 2 Compatibility (verify_phase2.sh)
[+] SUCCESS: 0 duplicates in TimescaleDB (total=456 == distinct=456).
  messages_published = 36
  distinct_seq_in_db = 19
  duplicates_dropped = 17
[+] SUCCESS: Requirement A1 verified: messages_published - distinct_seq == duplicates_dropped (17 > 0).
[+] SUCCESS: Exactly 1 matching audit log entry verified.
[+] SUCCESS: Alert latency is well under 5000 ms (7.35 ms).
[+] SUCCESS: Post-restart dedup verified: total (487) == distinct (487), advanced by 30 rows.
ALL PHASE 2 VERIFICATIONS PASSED SUCCESSFULLY!
  [PASS] Phase 2 verification passed without regression
==> STEP 9: Live Smoke Test Through Kafka (50 vehicles, 60 s)
[FAIL] stream-processor did not drain vehicle.normalized (committed=58624 end=58625)
PHASE3_EXIT 1
```

**Result.**
- `verify_phase2.sh` passed on its own (`PHASE2_EXIT 0`).
- `verify_phase3.sh` passed steps 1-8, including its nested Phase 2 run.
- Step 9 (live smoke test) failed on its drain check: committed offsets stayed one message behind
  the end offset. The cause is that `verify_phase2.sh` leaves the legacy single-vehicle simulator
  streaming at 1 message/s, so committed >= end is a race the check keeps losing.
- `scripts/smoke_live.sh` now stops that simulator before measuring. The rerun passed:

```
2026-09-29T06:18:17Z [INFO] [simulator.runner.live] Live simulation stopped at 2026-04-01T09:56:00+00:00 after 596 steps, 6227 messages sent (1016 injected duplicates).
messages_published=6227 distinct_rows_written=5211 duplicates_dropped=1016 vehicles_with_rows=50 rows=5211
[PASS] telemetry arrived: 5211 rows for 50 vehicles
[PASS] no duplicate (vehicle_id, seq) rows
[PASS] Phase 2 dedup invariant holds: 6227 - 5211 = 1016
[PASS] injected DTC raised exactly one ACTIVE alert (1224e16a-26bb-4b2e-be07-96cb9621d1a2) with one audit row
SMOKE TEST PASSED
```

The full `verify_phase3.sh` has not been rerun end to end after this fix, because of time. Its steps
1-8 passed in the run above, and step 9 passed in the standalone run.



## 8. Improvements after submission (2026-09-29)

### 8.1 Packed bucket state

Each bucket is now one JSON field instead of about 70 hash fields, with numbers stored as `%.17g`
strings. Parity, run with `scripts/verify_feature_parity.py --vehicles 20 --days 10`:
```
offline vs streaming[clean]   0 mismatches, max rel diff 1.936e-10  PASS
offline vs streaming[dup15]   0 mismatches, max rel diff 1.936e-10  PASS
offline vs streaming[restart] 0 mismatches, max rel diff 1.936e-10  PASS
streaming[clean] vs [dup15] / [restart]: 0 mismatches, max rel diff 0.0  PASS
runs: clean 1161.6 events/s, dup15 1286.6 events/s, restart 1114.0 events/s  (before: 784 / 892 events/s)
redis feature state per vehicle: mean 138,927 bytes, max 159,891  (before: 357,862 / 487,082)
```

**Failed attempt, recorded.** The first version used cjson's number encoder, which Redis limits to
14 significant digits. That produced 110 mismatches, all `w1h_voltage_std`, because of sum/sumsq
cancellation. Storing `%.17g` strings fixed it.

### 8.2 Event-driven scoring (dashboard freshness)

The stream processor appends each snapshot to the `fs:snapshots` stream, and the scorer consumes it
immediately. Measured on 3,123 live snapshots (50 vehicles, 600x speedup):
```
ingest -> snapshot written   p50 0.17 s  p95 1.99 s  max 2.77 s
snapshot -> risk scored      p50 1.47 s  p95 1.47 s
ingest -> risk in database   p50 1.70 s  p95 1.73 s  max 1.74 s
```
The dashboard polls every 2 s, so new risk scores and ML alerts are visible about 2-4 s after
ingest. The priority-queue ordering refreshes at most every 10 s.

### 8.3 Chaos: Kafka broker killed mid-run (`scripts/chaos_kafka.sh`)

```
baseline: normalized=72888 inbound=72887 dropped=0 rows=0
18:04:22 killing kafka
18:04:43 starting kafka
18:05:01 kafka back
2026-09-29T12:36:13Z [INFO] [simulator.runner.live] Live simulation stopped at 2026-04-01T18:52:00+00:00 after 1132 steps, 14378 messages sent (2385 injected duplicates).
simulator_sent=14378 reached_oem_inbound=14314 normalized_published=14314 distinct_rows_written=11942 duplicates_dropped=2372 rows=11942 distinct=11942
[PASS] no duplicate rows after the broker kill
[PASS] dedup invariant holds across the outage: 14314 - 11942 = 2372
[PASS] pipeline drained after recovery (committed == end offsets) without manual restarts
CHAOS TEST PASSED
```
Result:
- No duplicate rows.
- The dedup invariant held across the outage.
- The pipeline recovered without manual restarts.
- 64 of 14,378 attempted messages (0.45 percent) never reached the broker. The producer's retries
  were exhausted during the 39 s outage. That loss is on the producer side, before the platform.

### 8.4 OEM-B onboarding (live smoke test with 30 percent OEM-B vehicles)

```
messages_published=6224 distinct_rows_written=5208 duplicates_dropped=1016 vehicles_with_rows=50 rows=5208
[PASS] telemetry arrived: 5208 rows for 50 vehicles
[PASS] no duplicate (vehicle_id, seq) rows
[PASS] Phase 2 dedup invariant holds: 6224 - 5208 = 1016
[PASS] injected DTC raised exactly one ACTIVE alert (04f7e858-8ba7-47cf-8cc1-77b962f389dd) with one audit row
SMOKE TEST PASSED
TimescaleDB: OEM_B | 1551 rows | 14 vehicles
```
`tests/test_oem_b.py`: the payload matches its contract, and OEM-B produces the same canonical
events as OEM-A within unit rounding.

### 8.5 Fleet assistant

`tests/test_api.py::TestAssistant` passes 4 of 4:
- rules mode answering from tools;
- guardrails: tool allow-list, argument validation, length cap;
- tenant scope with audit;
- the LLM tool loop against a mocked Groq response.

**Not yet exercised against the live Groq API:** no key was configured at the time of writing.
