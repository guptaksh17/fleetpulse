#!/usr/bin/env bash
# =============================================================================
# Chaos test: kill the Kafka broker in the middle of a live run.
#   - 50 live-demo vehicles stream for 150 s wall clock (600x speedup)
#   - after 40 s the broker is killed (docker kill: SIGKILL, no clean shutdown), after 20 s it
#     is started again
#   - every pipeline service must reconnect on its own (no manual restarts)
# Pass criteria:
#   - no duplicate (vehicle_id, seq) rows in TimescaleDB
#   - the dedup invariant holds: published - distinct rows written == duplicates dropped
#   - every message on vehicle.normalized after recovery is consumed (committed == end)
# Reported (information): messages the simulator attempted vs. messages that reached the broker.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
export REDIS_HOST="${REDIS_HOST:-localhost}" REDIS_PORT="${REDIS_PORT:-6380}"
PY="${PYTHON:-.venv/bin/python}"; [ -x "$PY" ] || PY=python3
CONFIG="configs/live_demo.yaml"; VEHICLES=50

psql_ts() { docker exec timescaledb psql -U fleetpulse -d fleetpulse -t -A -c "$1" | tr -d ' '; }
end_offsets() { docker exec kafka /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic vehicle.normalized 2>/dev/null | awk -F':' '{s+=$3} END {print s+0}'; }
inbound_offsets() { docker exec kafka /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic oem.inbound 2>/dev/null | awk -F':' '{s+=$3} END {print s+0}'; }
committed() { docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group stream-processor 2>/dev/null | awk '$1=="stream-processor" && $4 ~ /^[0-9]+$/ {s+=$4} END {print s+0}'; }
dropped() { curl -s http://localhost:8080/metrics | grep -o -E '"duplicates_dropped": [0-9]+' | awk '{print $2}'; }
wait_kafka() { until docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list >/dev/null 2>&1; do sleep 2; done; }
wait_drained() { local d=$((SECONDS+600)); until [ "$(committed)" -ge "$(end_offsets)" ] && [ "$(end_offsets)" -gt 0 ]; do [ $SECONDS -ge $d ] && { echo "[FAIL] not drained"; exit 1; }; sleep 2; done; }

docker compose stop simulator >/dev/null 2>&1 || true
docker compose up -d kafka redis postgres timescaledb identity-resolver normalization rule-engine stream-processor >/dev/null 2>&1
wait_kafka
"$PY" scripts/seed_population.py --config "$CONFIG" >/dev/null 2>&1
"$PY" scripts/live_test_vehicles.py reset --config "$CONFIG" --vehicles "$VEHICLES"
VIDS=$("$PY" scripts/live_test_vehicles.py ids --config "$CONFIG" --vehicles "$VEHICLES" | "$PY" -c "import json,sys; print(','.join(\"'\"+v['vehicle_id']+\"'\" for v in json.load(sys.stdin)))")
wait_drained
S_OFF=$(end_offsets); S_IN=$(inbound_offsets); S_DROP=$(dropped); S_ROWS=$(psql_ts "SELECT count(*) FROM telemetry WHERE vehicle_id IN ($VIDS)")
echo "baseline: normalized=$S_OFF inbound=$S_IN dropped=$S_DROP rows=$S_ROWS"

echo "==> live run (150 s) with 20 percent duplicates; broker killed at +40 s, restarted at +60 s"
docker compose run --rm --no-deps -e DUPLICATE_RATE=0.2 simulator python -m simulator.vehicle_simulator \
  --config /app/configs/live_demo.yaml --vehicles "$VEHICLES" --speedup 600 --max-wall-seconds 150 > data/logs/chaos_simulator.log 2>&1 &
SIM=$!
sleep 40; echo "$(date +%T) killing kafka"; docker kill kafka >/dev/null
sleep 20; echo "$(date +%T) starting kafka"; docker start kafka >/dev/null; wait_kafka; echo "$(date +%T) kafka back"
wait $SIM || true
grep -E "Live simulation stopped" data/logs/chaos_simulator.log | cut -c1-200 || true
echo "==> waiting for the pipeline to drain after recovery"
sleep 10; wait_drained; sleep 3

E_OFF=$(end_offsets); E_IN=$(inbound_offsets); E_DROP=$(dropped)
ROWS=$(psql_ts "SELECT count(*) FROM telemetry WHERE vehicle_id IN ($VIDS)")
DISTINCT=$(psql_ts "SELECT count(*) FROM (SELECT DISTINCT vehicle_id, seq FROM telemetry WHERE vehicle_id IN ($VIDS)) d")
SENT=$(grep -o -E "[0-9]+ messages sent" data/logs/chaos_simulator.log | awk '{print $1}' | tail -1)
PUB=$((E_OFF - S_OFF)); NEW=$((DISTINCT - S_ROWS)); DROP=$((E_DROP - S_DROP)); INB=$((E_IN - S_IN))
echo "simulator_sent=${SENT:-?} reached_oem_inbound=$INB normalized_published=$PUB distinct_rows_written=$NEW duplicates_dropped=$DROP rows=$ROWS distinct=$DISTINCT"
[ "$ROWS" -eq "$DISTINCT" ] || { echo "[FAIL] duplicate rows after broker kill"; exit 1; }
echo "[PASS] no duplicate rows after the broker kill"
[ $((PUB - NEW)) -eq "$DROP" ] || { echo "[FAIL] dedup invariant: $PUB - $NEW != $DROP"; exit 1; }
echo "[PASS] dedup invariant holds across the outage: $PUB - $NEW = $DROP"
echo "[PASS] pipeline drained after recovery (committed == end offsets) without manual restarts"
"$PY" scripts/live_test_vehicles.py resolve --config "$CONFIG" --vehicles "$VEHICLES" >/dev/null
echo "CHAOS TEST PASSED"
