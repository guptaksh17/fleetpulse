#!/usr/bin/env bash
# =============================================================================
# FleetPulse Phase 4 Verification (features, labels, datasets)
#   1. Part A checks: history counts, lifecycle ordering, A1 equality test, resume equivalence
#   2. Live run: 50 history vehicles resumed from the offline checkpoint after feature
#      warm-up; component_features rows every simulated hour, no duplicates, sane counters
#   3. Offline features for the full history, dataset build, manifest table
#   4. Parity (streaming vs offline, with duplicate replay and restart), leakage, label and
#      the full unit test suite
#   5. verify_phase2.sh and verify_phase3.sh
#   6. Information only: stream-processor throughput and snapshot latency with features on
# Every step prints its evidence. The script stops at the first failure.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
# Host-side tools talk to the project Redis on its mapped port (see docker-compose.yml).
export REDIS_HOST="${REDIS_HOST:-localhost}" REDIS_PORT="${REDIS_PORT:-6380}"

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
LIVE_VEHICLES=50
LIVE_WALL_SECONDS="${LIVE_WALL_SECONDS:-120}"
LIVE_SPEEDUP="${LIVE_SPEEDUP:-600}"
HISTORY_END="2026-04-01T00:00:00Z"
CHECKPOINT="data/checkpoints/offline_checkpoint.json"
mkdir -p data/logs

step() { echo; echo "==> STEP $1: $2"; }
pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1"; exit 1; }
pg() { docker exec postgres psql -U fleetpulse -d fleetpulse -t -A -F '|' -c "$1"; }
ts() { docker exec timescaledb psql -U fleetpulse -d fleetpulse -t -A -F '|' -c "$1"; }

step 0 "Stack and schema"
docker compose up -d kafka kafka-init redis postgres timescaledb identity-resolver normalization rule-engine stream-processor >/dev/null 2>&1
docker exec -i postgres psql -U fleetpulse -d fleetpulse -q < db/postgres/migrations/004_maintenance_odometer.sql
docker exec -i timescaledb psql -U fleetpulse -d fleetpulse -q < db/timescale/migrations/002_component_features.sql 2>/dev/null
until curl -s http://localhost:8080/metrics >/dev/null; do sleep 2; done
pass "stack up, migrations applied"

# -----------------------------------------------------------------------------
step 1 "Part A checks"
if [ ! -f "$CHECKPOINT" ] || [ "$(pg "SELECT count(*) FROM maintenance_event WHERE occurred_at < '$HISTORY_END'")" -eq 0 ]; then
    echo "Default history missing: generating 500 vehicles x 90 days (seed 42)"
    "$PY" scripts/seed_population.py --config configs/offline_train.yaml 2>&1 | tail -1
    "$PY" scripts/generate_history.py --config configs/offline_train.yaml --days 90 --vehicles 500 --reset --checkpoint-out "$CHECKPOINT" 2>&1 | tail -1
fi
echo "History (TimescaleDB):"; ts "SELECT count(*) AS rows, count(DISTINCT vehicle_id) AS vehicles, min(event_ts), max(event_ts) FROM telemetry WHERE event_ts < '$HISTORY_END'"
echo "Ground truth (PostgreSQL) event_type|rows|with_odometer:"; pg "SELECT event_type, count(*), count(odometer_km) FROM maintenance_event WHERE occurred_at < '$HISTORY_END' GROUP BY 1 ORDER BY 1"
echo "Trips:"; pg "SELECT count(*) FROM trip WHERE started_at < '$HISTORY_END'"
ORDER=$(pg "WITH e AS (SELECT vehicle_component_id, event_type, LAG(event_type) OVER (PARTITION BY vehicle_component_id ORDER BY occurred_at, event_type) AS prev FROM maintenance_event)
SELECT count(*) FILTER (WHERE event_type = 'SERVICE_COMPLETED' AND (prev IS NULL OR prev = 'SERVICE_COMPLETED')),
       count(*) FILTER (WHERE event_type IN ('MAINTENANCE_REQUIRED','FAILURE') AND prev IN ('MAINTENANCE_REQUIRED','FAILURE')) FROM e")
echo "orphan_services|back_to_back_triggers = $ORDER"
[ "$ORDER" = "0|0" ] && pass "lifecycle ordering" || fail "lifecycle ordering violated: $ORDER"
"$PY" -m unittest tests.test_simulator_engine 2>&1 | grep -v " INFO " | tail -3
pass "A1 offline-vs-calibration equality, resume equivalence (unit), disjoint populations"
"$PY" scripts/verify_resume_equivalence.py --vehicles 50 --days 10 2>/dev/null | tail -12
pass "A3 resume equivalence (50 vehicles, 10 days)"

# -----------------------------------------------------------------------------
step 2 "Live run resumed from checkpoint with feature warm-up (${LIVE_VEHICLES} vehicles)"
"$PY" scripts/live_test_vehicles.py reset --config configs/offline_train.yaml --vehicles "$LIVE_VEHICLES" --after "$HISTORY_END"
"$PY" scripts/warm_feature_state.py --config configs/offline_train.yaml --vehicles "$LIVE_VEHICLES" --checkpoint "$CHECKPOINT" 2>/dev/null | tee data/logs/warmup.json
IDS=$("$PY" scripts/live_test_vehicles.py ids --config configs/offline_train.yaml --vehicles "$LIVE_VEHICLES" | "$PY" -c "import json,sys; print(','.join(\"'\"+v['vehicle_id']+\"'\" for v in json.load(sys.stdin)))")
M0=$(curl -s http://localhost:8080/metrics)
T0=$(date +%s)
docker compose run --rm --no-deps simulator python -m simulator.vehicle_simulator \
    --config /app/configs/offline_train.yaml --vehicles "$LIVE_VEHICLES" --checkpoint-in "/app/$CHECKPOINT" \
    --speedup "$LIVE_SPEEDUP" --max-wall-seconds "$LIVE_WALL_SECONDS" 2>&1 | grep -E "Restored checkpoint|Live simulation" || true
end_off() { docker exec kafka /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic vehicle.normalized | awk -F':' '{s+=$3} END {print s+0}'; }
committed() { docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group stream-processor 2>/dev/null | awk '$1=="stream-processor" && $4 ~ /^[0-9]+$/ {s+=$4} END {print s+0}'; }
until [ "$(committed)" -ge "$(end_off)" ]; do sleep 2; done
T1=$(date +%s)
M1=$(curl -s http://localhost:8080/metrics)
echo "Live telemetry rows after the history end:"; ts "SELECT count(*), min(event_ts), max(event_ts) FROM telemetry WHERE vehicle_id IN ($IDS) AND event_ts >= '$HISTORY_END'"
echo "component_features per simulated hour (feature_ts|rows|vehicles|window_complete_rows):"
ts "SELECT feature_ts, count(*), count(DISTINCT vehicle_id), count(*) FILTER (WHERE window_complete) FROM component_features WHERE vehicle_id IN ($IDS) AND feature_ts >= '$HISTORY_END' GROUP BY 1 ORDER BY 1" | tee data/logs/live_features_per_hour.txt
EXPECTED=$(pg "SELECT count(*) FROM vehicle_component WHERE status = 'ACTIVE' AND vehicle_id IN ($IDS)")
HOURS=$(wc -l < data/logs/live_features_per_hour.txt | tr -d ' ')
BAD_HOURS=$(awk -F'|' -v e="$EXPECTED" '$2 != e || $4 != e' data/logs/live_features_per_hour.txt | wc -l | tr -d ' ')
DUP=$(ts "SELECT count(*) - count(DISTINCT (vehicle_id, component, feature_ts)) FROM component_features WHERE vehicle_id IN ($IDS)")
echo "hours=$HOURS expected_rows_per_hour=$EXPECTED hours_not_matching=$BAD_HOURS duplicate_rows=$DUP"
[ "$HOURS" -ge 3 ] || fail "fewer than 3 simulated hours of snapshots"
[ "$BAD_HOURS" -eq 0 ] || fail "some hours miss snapshots or are not window_complete"
[ "$DUP" -eq 0 ] || fail "duplicate component_features rows"
pass "a complete, window_complete snapshot for every ACTIVE component every simulated hour; no duplicates"
echo "Feature counters before: $(echo "$M0" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("features"))')"
echo "Feature counters after:  $(echo "$M1" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("features"))')"
"$PY" - "$M0" "$M1" "$((T1 - T0))" <<'PYEOF' | tee data/logs/live_throughput.json
import json, sys
a, b, secs = json.loads(sys.argv[1]).get("features", {}), json.loads(sys.argv[2]).get("features", {}), int(sys.argv[3])
d = {k: b.get(k, 0) - a.get(k, 0) for k in ("events_applied", "duplicates_skipped", "late_events_ignored", "snapshots_written")}
print(json.dumps({"window_seconds": secs, **d, "events_applied_per_second": round(d["events_applied"] / max(1, secs), 1),
                  "snapshot_latency_ms_p50": b.get("snapshot_latency_ms_p50"), "snapshot_latency_ms_p95": b.get("snapshot_latency_ms_p95")}))
assert d["events_applied"] > 0 and d["snapshots_written"] > 0 and d["late_events_ignored"] == 0, d
PYEOF
pass "counters sane (events applied, snapshots written, no late events)"
"$PY" scripts/live_test_vehicles.py resolve --config configs/offline_train.yaml --vehicles "$LIVE_VEHICLES"

# -----------------------------------------------------------------------------
step 3 "Offline features for the full history and dataset build"
"$PY" scripts/build_features_offline.py --out data/features/f1 2>/dev/null | tee data/logs/build_features_offline.json
"$PY" scripts/build_dataset.py 2>&1 | grep -v -E "UserWarning|read_sql|warnings.warn" | tee data/logs/build_dataset.log
grep -q "GUARDRAIL WARNINGS" data/logs/build_dataset.log && echo "[WARN] calibration guardrail triggered, see above" || pass "calibration guardrail"

# -----------------------------------------------------------------------------
step 4 "Parity, leakage, labels and the unit test suite"
"$PY" scripts/verify_feature_parity.py --vehicles 20 --days 10 2>&1 | grep -v -E " INFO |FutureWarning|to_datetime" | tee data/logs/feature_parity.log
"$PY" -m unittest tests.test_leakage tests.test_labels tests.test_splits tests.test_hidden_state tests.test_feature_lua tests.test_feature_stats tests.test_feature_defs -v 2>&1 | grep -E "(ok|FAIL|ERROR|skipped)$|^Ran|^OK|^FAILED"
"$PY" -m unittest discover -s tests > data/logs/unittest_phase4.log 2>&1 || { grep -E "^(FAIL|ERROR):" -A20 data/logs/unittest_phase4.log; tail -3 data/logs/unittest_phase4.log; exit 1; }
tail -3 data/logs/unittest_phase4.log
pass "parity, leakage, label and unit tests"

# -----------------------------------------------------------------------------
step 5 "Earlier phases"
bash scripts/verify_phase2.sh 2>&1 | tail -4
bash scripts/verify_phase3.sh 2>&1 | grep -E "STEP|PASS|FAIL|COMPLETE" | sed 's/\x1b\[[0-9;]*m//g'

echo
echo "PHASE 4 VERIFICATION COMPLETE"
