# FleetPulse Phase 3 Verification (corrected in Phase 4 Part A)

## Correction notice

The previous version of this document reported "ALL CHECKS PASSED". Several of those checks had not
been executed, and some could not have passed:
- The offline generator never persisted a maintenance event.
- The live runner could not start.
- The "ROC-AUC > 0.85" check measured the opposite of the intended property.

This version lists only checks that were actually run, with their output.

## A1. Zero maintenance events: cause and fix

Reproduction (200 vehicles, 30 days, original code, run on 2026-09-29):
```
wall_s 34.6 events {}
save_maintenance_events raised: AttributeError 'NoneType' object has no attribute 'event_id'
```

There were two independent defects:
1. **No events.** Degradation was too slow for any component to reach the maintenance threshold in
   30 days.
2. **Sink crash.** The Postgres sink read `MaintenanceEventRecord.event_id`, which did not exist,
   so the first event of any longer run would have crashed.

Contributing problems:
- The offline runner started at `now() - days`, ignoring `clock.start_time`.
- Most `degradation.*` settings were never passed to the component models.

Fix:
- One shared `FleetEngine` step loop used by the offline, live and calibration paths.
- Deterministic event ids.
- Events carry the component id and odometer.
- Every degradation parameter comes from config.

Test `tests/test_simulator_engine.py::test_offline_events_equal_calibration_events` passes
(60 vehicles, 30 days, events non-empty and identical).

## A2. Calibration

`docs/phase3-calibration.md` (200 vehicles, 90 days):

| Check | Result |
|---|---|
| Max single-signal ROC-AUC (target <= 0.85) | 0.6935 |
| Prevalence per component type (25-40 percent) | BRAKE 38.0, POWERTRAIN 39.5, BATTERY 39.8 |
| Hourly positive rate (3-8 percent) | 3.21 / 3.18 / 3.19 |
| Sudden failure ratio (< 15 percent) | 7.0 |
| Trigger events per third of the run | 67 / 76 / 57 (stationary) |

Before tuning, the same fleet showed 90 percent sudden failures, 97 percent battery prevalence and
0.25 percent powertrain hourly positives.

## A3. Additional verification

**Lifecycle ordering** on the regenerated default history:
```
 orphan_services | back_to_back_triggers
-----------------+-----------------------
               0 |                     0
```

**Resume equivalence** (in-memory unit test, 8 vehicles x 4 days, continuous vs checkpoint and
resume): identical SHA-256 digests of all telemetry and event lines. The script
`scripts/verify_resume_equivalence.py` runs the 50-vehicle, 10-day version, and
`scripts/verify_phase4.sh` executes it.

**Live smoke test through Kafka** (`scripts/smoke_live.sh`, 50 vehicles, 60 s wall clock, 600x
speedup):
```
Live simulation stopped at 2026-04-01T09:52:00+00:00 after 592 steps, 6215 messages sent (1016 injected duplicates).
messages_published=6215 distinct_rows_written=5199 duplicates_dropped=1016 vehicles_with_rows=50 rows=5199
[PASS] telemetry arrived: 5199 rows for 50 vehicles
[PASS] no duplicate (vehicle_id, seq) rows
[PASS] Phase 2 dedup invariant holds: 6215 - 5199 = 1016
[PASS] injected DTC raised exactly one ACTIVE alert (a6126a7f-a7e2-4226-a6f0-c969d693abb8) with one audit row
SMOKE TEST PASSED
```

**Incident found by this test.** Its cleanup step deleted history for 17 training vehicles, because
the live-demo and offline populations shared VINs. VINs derive from a serial number, not the seed.
The populations now use disjoint `vin_serial_start` ranges, enforced by a test, and the default
history was regenerated. The regenerated counts are identical to the first run, confirming
determinism.

## A4. Odometer on service records

```
      event_type      |  n  | with_odometer
----------------------+-----+---------------
 FAILURE              |  33 |            33
 MAINTENANCE_REQUIRED | 462 |           462
 SERVICE_COMPLETED    | 485 |           485
```

## A5. Default history (500 vehicles, 90 days, seed 42)

```
Offline simulation finished: {'vehicles': 500, 'days': 90, 'telemetry_rows': 12172824, 'trips': 138593,
 'maintenance_events': 980, 'event_types': {'MAINTENANCE_REQUIRED': 462, 'FAILURE': 33, 'SERVICE_COMPLETED': 485}, ...
 'component_prevalence_pct': 39.0, 'sudden_ratio_pct': 6.67}
real 1037.35   (first run; the regeneration took 1332 s under load)
```

TimescaleDB: 12,172,824 rows for 500 vehicles, 2026-01-01 00:00 to 2026-03-31 23:45.
