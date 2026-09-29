#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# FleetPulse Phase 2 Verification Script
# Validates Topic Topology, Stream Processor Dedup, Rule Engine, and Alerting
# ==============================================================================

DEMO_VEHICLE_ID="00000000-0000-0000-0000-000000000003"
export INJECT_DTC_CODE="BRAKE_SYSTEM_CRITICAL"
export INJECT_DTC_AFTER_S="2"
export INJECT_DTC_DURATION_S="30"
export DUPLICATE_RATE="0.2"

echo "========================================================================"
echo "Starting FleetPulse Phase 2 Verification"
echo "Target Vehicle ID: ${DEMO_VEHICLE_ID}"
echo "Injected DTC: ${INJECT_DTC_CODE}"
echo "Duplicate Rate: ${DUPLICATE_RATE}"
echo "========================================================================"

# Step 1: Ensure containers are running with required simulation flags
echo ""
echo "[Step 1/5] Determining current sequence in TimescaleDB, capturing start offsets & metrics, and updating simulator..."
CURRENT_MAX_SEQ=$(docker exec timescaledb psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COALESCE(MAX(seq), 0) FROM telemetry WHERE vehicle_id = '${DEMO_VEHICLE_ID}';" | tr -d ' ')
export START_SEQ="${CURRENT_MAX_SEQ:-0}"
echo "Current max seq in TimescaleDB: ${START_SEQ}. Starting simulator from sequence ${START_SEQ}."

get_normalized_offsets() {
    docker exec kafka /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic vehicle.normalized | awk -F':' '{sum += $3} END {print sum}'
}

get_consumer_committed_offsets() {
    docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group stream-processor | awk '$1 == "stream-processor" {sum += $4} END {print sum}'
}

get_stream_processor_metrics() {
    curl -s http://localhost:8080/metrics || true
}

echo "Starting simulator with configured injection parameters..."
docker compose up -d simulator

echo "Freezing simulator to establish exact window baseline..."
docker compose pause simulator
while true; do
    COMMITTED=$(get_consumer_committed_offsets)
    END_OFF=$(get_normalized_offsets)
    if [ -n "${COMMITTED}" ] && [ -n "${END_OFF}" ] && [ "${COMMITTED}" -ge "${END_OFF}" ]; then
        break
    fi
    sleep 0.5
done

START_OFFSETS=$(get_normalized_offsets)
START_METRICS=$(get_stream_processor_metrics)
START_DROPPED=$(echo "${START_METRICS}" | grep -o -E '"duplicates_dropped": [0-9]+' | awk '{print $2}' || echo "0")
START_DISTINCT_ROWS=$(docker exec timescaledb psql -U fleetpulse -d fleetpulse -t -A -c "SELECT count(DISTINCT seq) FROM telemetry WHERE vehicle_id = '${DEMO_VEHICLE_ID}';" | tr -d ' ')

echo "Window start: offsets=${START_OFFSETS}, distinct_rows=${START_DISTINCT_ROWS}, duplicates_dropped=${START_DROPPED}"
docker compose unpause simulator

echo ""
echo "[Step 2/5] Waiting 30 seconds for telemetry, duplicates, and alerts to flow..."
for i in {30..1}; do
    echo -ne "Waiting... ${i}s remaining\033[0K\r"
    sleep 1
done
echo ""

echo "Pausing simulator to freeze window and drain consumer pipeline..."
docker compose pause simulator

echo "Waiting for stream-processor consumer to catch up..."
while true; do
    COMMITTED=$(get_consumer_committed_offsets)
    END_OFFSETS=$(get_normalized_offsets)
    if [ -n "${COMMITTED}" ] && [ -n "${END_OFFSETS}" ] && [ "${COMMITTED}" -ge "${END_OFFSETS}" ]; then
        break
    fi
    sleep 0.5
done

# Step 3: Verify Deduplication in TimescaleDB & Offsets Accounting (Requirement A1)
echo ""
echo "[Step 3/5] Verifying Deduplication in TimescaleDB & Offset Accounting (Requirement A1)..."
TS_QUERY="SELECT count(*), count(DISTINCT seq) FROM telemetry WHERE vehicle_id = '${DEMO_VEHICLE_ID}';"
TS_RESULT=$(docker exec timescaledb psql -U fleetpulse -d fleetpulse -t -A -F "|" -c "${TS_QUERY}")

TOTAL_COUNT=$(echo "${TS_RESULT}" | cut -d'|' -f1 | tr -d ' ')
DISTINCT_COUNT=$(echo "${TS_RESULT}" | cut -d'|' -f2 | tr -d ' ')

echo "TimescaleDB telemetry: total_rows=${TOTAL_COUNT}, distinct_seq=${DISTINCT_COUNT}"

if [ -z "${TOTAL_COUNT}" ] || [ "${TOTAL_COUNT}" -eq 0 ]; then
    echo "[-] FAILED: No telemetry rows found in TimescaleDB!"
    docker compose logs --tail=50 stream-processor
    exit 1
fi

if [ "${TOTAL_COUNT}" -ne "${DISTINCT_COUNT}" ]; then
    echo "[-] FAILED: Duplicate rows detected in TimescaleDB!"
    echo "    Total count (${TOTAL_COUNT}) != Distinct count (${DISTINCT_COUNT})"
    exit 1
fi
echo "[+] SUCCESS: 0 duplicates in TimescaleDB (total=${TOTAL_COUNT} == distinct=${DISTINCT_COUNT})."

# Requirement A1: Offset and dropped counter delta check
END_OFFSETS=$(get_normalized_offsets)
END_METRICS=$(get_stream_processor_metrics)
END_DROPPED=$(echo "${END_METRICS}" | grep -o -E '"duplicates_dropped": [0-9]+' | awk '{print $2}' || echo "0")
END_DISTINCT_ROWS=${DISTINCT_COUNT}

MESSAGES_PUBLISHED=$((END_OFFSETS - START_OFFSETS))
DISTINCT_IN_DB=$((END_DISTINCT_ROWS - START_DISTINCT_ROWS))
DROPPED_DELTA=$((END_DROPPED - START_DROPPED))

echo "Requirement A1 Accounting over test window:"
echo "  messages_published = ${MESSAGES_PUBLISHED}"
echo "  distinct_seq_in_db = ${DISTINCT_IN_DB}"
echo "  duplicates_dropped = ${DROPPED_DELTA}"

EXPECTED_DROPPED=$((MESSAGES_PUBLISHED - DISTINCT_IN_DB))
if [ "${EXPECTED_DROPPED}" -ne "${DROPPED_DELTA}" ]; then
    echo "[-] FAILED: Invariant violated! messages_published (${MESSAGES_PUBLISHED}) - distinct_seq (${DISTINCT_IN_DB}) = ${EXPECTED_DROPPED} != duplicates_dropped (${DROPPED_DELTA})"
    exit 1
fi

if [ "${DROPPED_DELTA}" -le 0 ]; then
    echo "[-] FAILED: duplicates_dropped delta (${DROPPED_DELTA}) must be > 0 with DUPLICATE_RATE=0.2!"
    exit 1
fi
echo "[+] SUCCESS: Requirement A1 verified: messages_published - distinct_seq == duplicates_dropped (${DROPPED_DELTA} > 0)."

docker compose unpause simulator

# Step 4: Verify Alert and Audit Log in PostgreSQL & Latency
echo ""
echo "[Step 4/5] Verifying Alert and Audit Log in PostgreSQL..."
ALERT_QUERY="SELECT alert_id, alert_type, status, severity FROM alert WHERE alert_type = 'DTC_${INJECT_DTC_CODE}' AND status = 'ACTIVE';"
ALERT_RESULT=$(docker exec postgres psql -U fleetpulse -d fleetpulse -t -A -F "|" -c "${ALERT_QUERY}")

ALERT_COUNT=$(docker exec postgres psql -U fleetpulse -d fleetpulse -t -A -c "SELECT count(*) FROM alert WHERE alert_type = 'DTC_${INJECT_DTC_CODE}' AND status = 'ACTIVE';" | tr -d ' ')
echo "PostgreSQL active alerts for DTC_${INJECT_DTC_CODE}: ${ALERT_COUNT}"

if [ "${ALERT_COUNT}" -ne 1 ]; then
    echo "[-] FAILED: Expected exactly 1 active alert for DTC_${INJECT_DTC_CODE}, found ${ALERT_COUNT}"
    docker exec postgres psql -U fleetpulse -d fleetpulse -c "SELECT * FROM alert;"
    docker compose logs --tail=50 rule-engine
    exit 1
fi

ALERT_ID=$(echo "${ALERT_RESULT}" | cut -d'|' -f1 | tr -d ' ')
echo "[+] Active alert found: alert_id=${ALERT_ID}"

AUDIT_COUNT=$(docker exec postgres psql -U fleetpulse -d fleetpulse -t -A -c "SELECT count(*) FROM audit_log WHERE entity_id = '${ALERT_ID}' AND action = 'ALERT_CREATED';" | tr -d ' ')
echo "PostgreSQL audit logs for alert_id ${ALERT_ID}: ${AUDIT_COUNT}"

if [ "${AUDIT_COUNT}" -ne 1 ]; then
    echo "[-] FAILED: Expected exactly 1 audit log entry for alert ${ALERT_ID}, found ${AUDIT_COUNT}"
    docker exec postgres psql -U fleetpulse -d fleetpulse -c "SELECT * FROM audit_log;"
    exit 1
fi
echo "[+] SUCCESS: Exactly 1 matching audit log entry verified."

echo "Checking rule engine latency..."
LATENCY_LOG=$(docker compose logs rule-engine | grep -E "ALERT GENERATED.*latency:" | tail -n 1 || true)
if [ -z "${LATENCY_LOG}" ]; then
    echo "[-] WARNING: Could not find 'ALERT GENERATED' latency log in rule-engine output."
else
    echo "Log: ${LATENCY_LOG}"
    LATENCY_VAL=$(echo "${LATENCY_LOG}" | grep -o -E "latency: [0-9.]+" | awk '{print $2}')
    echo "Parsed Latency: ${LATENCY_VAL} ms"
    LATENCY_INT=$(echo "${LATENCY_VAL}" | cut -d'.' -f1)
    if [ "${LATENCY_INT}" -ge 5000 ]; then
        echo "[-] FAILED: Rule engine latency ${LATENCY_VAL}ms exceeded 5000ms threshold!"
        exit 1
    fi
    echo "[+] SUCCESS: Alert latency is well under 5000 ms (${LATENCY_VAL} ms)."
fi

# Step 5: Crash/Restart Resilience Test
echo ""
echo "[Step 5/5] Testing stream-processor restart resilience mid-run..."
PREV_COUNT=$(docker exec timescaledb psql -U fleetpulse -d fleetpulse -t -A -c "SELECT count(*) FROM telemetry WHERE vehicle_id = '${DEMO_VEHICLE_ID}';" | tr -d ' ')
echo "Telemetry rows before stream-processor restart: ${PREV_COUNT}"

echo "Restarting stream-processor container..."
docker compose restart stream-processor

echo "Waiting 20 seconds for stream processor to rejoin and consume more events..."
for i in {20..1}; do
    echo -ne "Waiting... ${i}s remaining\033[0K\r"
    sleep 1
done
echo ""

POST_RESULT=$(docker exec timescaledb psql -U fleetpulse -d fleetpulse -t -A -F "|" -c "${TS_QUERY}")
POST_TOTAL=$(echo "${POST_RESULT}" | cut -d'|' -f1 | tr -d ' ')
POST_DISTINCT=$(echo "${POST_RESULT}" | cut -d'|' -f2 | tr -d ' ')

echo "Post-restart telemetry: total_rows=${POST_TOTAL}, distinct_seq=${POST_DISTINCT}"

if [ "${POST_TOTAL}" -ne "${POST_DISTINCT}" ]; then
    echo "[-] FAILED: Duplicate rows detected after restart!"
    echo "    Total count (${POST_TOTAL}) != Distinct count (${POST_DISTINCT})"
    exit 1
fi

if [ "${POST_TOTAL}" -le "${PREV_COUNT}" ]; then
    echo "[-] FAILED: Telemetry did not advance after restart! Prev=${PREV_COUNT}, Post=${POST_TOTAL}"
    docker compose logs --tail=30 stream-processor
    exit 1
fi

echo "[+] SUCCESS: Post-restart dedup verified: total (${POST_TOTAL}) == distinct (${POST_DISTINCT}), advanced by $((POST_TOTAL - PREV_COUNT)) rows."

echo ""
echo "========================================================================"
echo "ALL PHASE 2 VERIFICATIONS PASSED SUCCESSFULLY!"
echo "========================================================================"
exit 0
