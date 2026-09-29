#!/usr/bin/env bash
# =============================================================================
# FleetPulse live smoke test through Kafka (Phase 3 Part A3)
#   - 50 vehicles from configs/live_demo.yaml (own seed, own VINs), 60 s wall clock
#   - telemetry rows arrive in TimescaleDB for those vehicles, with no duplicates
#   - an injected BRAKE_SYSTEM_CRITICAL DTC raises exactly one ACTIVE alert and one audit row
#   - Phase 2 dedup invariant over the window:
#       messages_published - distinct_rows_written == duplicates_dropped
# The script resets only its own test vehicles first, so it can be re-run.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
# Host-side tools talk to the project Redis on its mapped port (see docker-compose.yml).
export REDIS_HOST="${REDIS_HOST:-localhost}" REDIS_PORT="${REDIS_PORT:-6380}"

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
CONFIG="configs/live_demo.yaml"
VEHICLES=50
WALL_SECONDS="${SMOKE_WALL_SECONDS:-60}"
SPEEDUP="${SMOKE_SPEEDUP:-600}"
DTC="BRAKE_SYSTEM_CRITICAL"

fail() { echo "[FAIL] $1"; exit 1; }
pass() { echo "[PASS] $1"; }
psql_pg() { docker exec postgres psql -U fleetpulse -d fleetpulse -t -A -c "$1" | tr -d ' '; }
psql_ts() { docker exec timescaledb psql -U fleetpulse -d fleetpulse -t -A -c "$1" | tr -d ' '; }
normalized_end_offsets() {
    docker exec kafka /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic vehicle.normalized | awk -F':' '{sum += $3} END {print sum+0}'
}
committed_offsets() {
    docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group stream-processor 2>/dev/null | awk '$1 == "stream-processor" && $4 ~ /^[0-9]+$/ {sum += $4} END {print sum+0}'
}
dropped() { curl -s http://localhost:8080/metrics | grep -o -E '"duplicates_dropped": [0-9]+' | awk '{print $2}'; }
wait_drained() {
    local deadline=$((SECONDS + 600))
    while true; do
        local c e
        c=$(committed_offsets); e=$(normalized_end_offsets)
        [ -n "$c" ] && [ -n "$e" ] && [ "$c" -ge "$e" ] && break
        [ $SECONDS -ge $deadline ] && fail "stream-processor did not drain vehicle.normalized (committed=$c end=$e)"
        sleep 2
    done
}

echo "==> Stopping the long-running legacy simulator (verify_phase2.sh leaves it streaming), so offsets can drain"
docker compose stop simulator >/dev/null 2>&1 || true

echo "==> Starting pipeline services"
docker compose up -d --build kafka kafka-init redis postgres timescaledb identity-resolver normalization rule-engine stream-processor >/dev/null
docker compose build simulator >/dev/null
until curl -s http://localhost:8080/metrics >/dev/null; do sleep 2; done

echo "==> Seeding and resetting the ${VEHICLES} smoke-test vehicles"
"$PY" scripts/seed_population.py --config "$CONFIG" 2>&1 | tail -1
"$PY" scripts/live_test_vehicles.py reset --config "$CONFIG" --vehicles "$VEHICLES"
IDS_JSON=$("$PY" scripts/live_test_vehicles.py ids --config "$CONFIG" --vehicles "$VEHICLES")
VID_LIST=$(echo "$IDS_JSON" | "$PY" -c "import json,sys; print(','.join(\"'\"+v['vehicle_id']+\"'\" for v in json.load(sys.stdin)))")
INJ_BRAKE=$(echo "$IDS_JSON" | "$PY" -c "import json,sys; print(json.load(sys.stdin)[0]['components']['BRAKE'])")

wait_drained
START_OFF=$(normalized_end_offsets)
START_DROP=$(dropped)
START_ROWS=$(psql_ts "SELECT count(*) FROM telemetry WHERE vehicle_id IN (${VID_LIST})")
echo "Window start: normalized offsets=${START_OFF}, duplicates_dropped=${START_DROP}, rows=${START_ROWS}"

echo "==> Running live simulator: ${VEHICLES} vehicles, ${WALL_SECONDS} s wall, speedup ${SPEEDUP}x, DTC ${DTC}, duplicate rate 0.2"
docker compose run --rm --no-deps \
    -e INJECT_DTC_CODE="$DTC" -e DUPLICATE_RATE=0.2 \
    simulator python -m simulator.vehicle_simulator \
    --config /app/configs/live_demo.yaml --vehicles "$VEHICLES" \
    --speedup "$SPEEDUP" --max-wall-seconds "$WALL_SECONDS" 2>&1 | grep -E "Live simulation" || true

echo "==> Waiting for stream-processor to drain"
wait_drained
sleep 3
END_OFF=$(normalized_end_offsets)
END_DROP=$(dropped)
ROWS=$(psql_ts "SELECT count(*) FROM telemetry WHERE vehicle_id IN (${VID_LIST})")
DISTINCT=$(psql_ts "SELECT count(*) FROM (SELECT DISTINCT vehicle_id, seq FROM telemetry WHERE vehicle_id IN (${VID_LIST})) d")
VEH_WITH_ROWS=$(psql_ts "SELECT count(DISTINCT vehicle_id) FROM telemetry WHERE vehicle_id IN (${VID_LIST})")

PUBLISHED=$((END_OFF - START_OFF))
NEW_DISTINCT=$((DISTINCT - START_ROWS))
DROP_DELTA=$((END_DROP - START_DROP))
echo "messages_published=${PUBLISHED} distinct_rows_written=${NEW_DISTINCT} duplicates_dropped=${DROP_DELTA} vehicles_with_rows=${VEH_WITH_ROWS} rows=${ROWS}"

[ "$ROWS" -gt 0 ] || fail "no telemetry rows arrived for the smoke-test vehicles"
pass "telemetry arrived: ${ROWS} rows for ${VEH_WITH_ROWS} vehicles"
[ "$ROWS" -eq "$DISTINCT" ] || fail "duplicate rows in TimescaleDB (${ROWS} != ${DISTINCT})"
pass "no duplicate (vehicle_id, seq) rows"
[ $((PUBLISHED - NEW_DISTINCT)) -eq "$DROP_DELTA" ] || fail "dedup invariant violated: ${PUBLISHED} - ${NEW_DISTINCT} != ${DROP_DELTA}"
[ "$DROP_DELTA" -gt 0 ] || fail "expected injected duplicates to be dropped (duplicates_dropped delta = 0)"
pass "Phase 2 dedup invariant holds: ${PUBLISHED} - ${NEW_DISTINCT} = ${DROP_DELTA}"

ALERTS=$(psql_pg "SELECT count(*) FROM alert WHERE vehicle_component_id = '${INJ_BRAKE}' AND alert_type = 'DTC_${DTC}' AND status = 'ACTIVE'")
[ "$ALERTS" -eq 1 ] || fail "expected exactly 1 ACTIVE DTC_${DTC} alert for the injected vehicle, found ${ALERTS}"
ALERT_ID=$(psql_pg "SELECT alert_id FROM alert WHERE vehicle_component_id = '${INJ_BRAKE}' AND alert_type = 'DTC_${DTC}' AND status = 'ACTIVE'")
AUDITS=$(psql_pg "SELECT count(*) FROM audit_log WHERE entity_id = '${ALERT_ID}' AND action = 'ALERT_CREATED'")
[ "$AUDITS" -eq 1 ] || fail "expected exactly 1 audit row for alert ${ALERT_ID}, found ${AUDITS}"
pass "injected DTC raised exactly one ACTIVE alert (${ALERT_ID}) with one audit row"

FEATURE_ROWS=$(psql_ts "SELECT count(*) FROM component_features WHERE vehicle_id IN (${VID_LIST})")
echo "[INFO] component_features rows for smoke vehicles: ${FEATURE_ROWS}"

"$PY" scripts/live_test_vehicles.py resolve --config "$CONFIG" --vehicles "$VEHICLES"
echo "SMOKE TEST PASSED"
