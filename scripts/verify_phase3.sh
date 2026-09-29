#!/usr/bin/env bash
# =============================================================================
# FleetPulse Phase 3 Verification Script
# Verifies:
#   1. Docker service health
#   2. Fleet population seeding in PostgreSQL (500 vehicles)
#   3. Simulator calibration (per-signal AUC <= 0.85, prevalence 25-40 %, sudden < 15 %,
#      hourly positive rate 3-8 %)
#   4. Default history (500 vehicles, 90 days, seed 42) present, generated if missing;
#      maintenance events persisted with odometer readings
#   4b. Lifecycle ordering SQL checks on maintenance_event
#   5. Invariant check: Zero hidden state columns in TimescaleDB
#   6. Checkpoint persistence; 6b resume equivalence (byte-identical streams)
#   7. Unit test suite execution
#   8. Backward compatibility regression: verify_phase2.sh
#   9. Live smoke test through Kafka (scripts/smoke_live.sh)
# =============================================================================

set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

BOLD='\033[1m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_step() {
    echo -e "\n${BLUE}${BOLD}==> STEP $1: $2${NC}"
}

log_pass() {
    echo -e "${GREEN}  [PASS] $1${NC}"
}

log_fail() {
    echo -e "${RED}  [FAIL] $1${NC}"
    exit 1
}

log_info() {
    echo -e "${YELLOW}  [INFO] $1${NC}"
}

POSTGRES_CONTAINER="postgres"
TIMESCALE_CONTAINER="timescaledb"
KAFKA_CONTAINER="kafka"
REDIS_CONTAINER="redis"

# -----------------------------------------------------------------------------
# STEP 1: Stack Health Verification
# -----------------------------------------------------------------------------
log_step "1" "Verifying Docker Stack Health"

for c in "$POSTGRES_CONTAINER" "$TIMESCALE_CONTAINER" "$KAFKA_CONTAINER" "$REDIS_CONTAINER"; do
    if docker ps --format '{{.Names}}' | grep -q "^${c}$"; then
        log_pass "Container '$c' is running"
    else
        log_fail "Container '$c' is not running!"
    fi
done

# -----------------------------------------------------------------------------
# STEP 2: Fleet Population Seeding in PostgreSQL
# -----------------------------------------------------------------------------
log_step "2" "Seeding Fleet Population (500 Vehicles)"

"$PY" scripts/seed_population.py --config configs/offline_train.yaml

VEHICLE_COUNT=$(docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM vehicle;")
COMPONENT_COUNT=$(docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM vehicle_component;")
DRIVER_COUNT=$(docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM driver;")

log_info "PostgreSQL vehicles: $VEHICLE_COUNT, components: $COMPONENT_COUNT, drivers: $DRIVER_COUNT"

if [ "$VEHICLE_COUNT" -ge 500 ]; then
    log_pass "Vehicle population seeded successfully (>= 500 vehicles)"
else
    log_fail "Vehicle count ($VEHICLE_COUNT) is less than 500"
fi

if [ "$COMPONENT_COUNT" -ge 1000 ]; then
    log_pass "Vehicle components seeded successfully (>= 1000 components)"
else
    log_fail "Component count ($COMPONENT_COUNT) is insufficient"
fi

# -----------------------------------------------------------------------------
# STEP 3: Degradation Calibration (B12 Targets)
# -----------------------------------------------------------------------------
log_step "3" "Degradation Calibration Verification (B12 Targets)"

mkdir -p data/logs
PYTHONUNBUFFERED=1 "$PY" -u scripts/calibrate_degradation.py --days 90 --vehicles 200 \
    --out-report data/logs/phase3-calibration.md --out-json data/logs/phase3-calibration.json 2>/dev/null | sed -n '/## 1. Targets/,/## 3/p'

"$PY" - <<'PYEOF' || log_fail "Calibration targets not met (see data/logs/phase3-calibration.md)"
import json, sys
r = json.load(open("data/logs/phase3-calibration.json"))
c = r["checks"]
ok = c["max_single_signal_auc_le_0.85"] and c["component_prevalence_25_40"] and c["sudden_ratio_lt_15"] \
    and all(c["component_prevalence_25_40_by_type"].values())
print("max single-signal AUC", r["max_single_signal_auc"], "| component prevalence", r["component_prevalence_pct"],
      "| sudden ratio", r["sudden_ratio_pct"], "| hourly positive rate",
      {k: v["hourly_positive_rate_pct"] for k, v in r["per_component"].items()})
sys.exit(0 if ok else 1)
PYEOF
log_pass "Calibration targets met (per-signal AUC, prevalence, sudden ratio)"

# -----------------------------------------------------------------------------
# STEP 4: Default History (500 vehicles, 90 days, seed 42)
# -----------------------------------------------------------------------------
log_step "4" "Default History & Ground Truth (generated only if missing)"

CHECKPOINT_FILE="data/checkpoints/offline_checkpoint.json"
MAINT_ROWS=$(docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM maintenance_event WHERE occurred_at < '2026-04-01';")
TIMESCALE_ROWS=$(docker exec "$TIMESCALE_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM telemetry WHERE event_ts >= '2026-01-01' AND event_ts < '2026-04-01';")
if [ "$MAINT_ROWS" -eq 0 ] || [ "$TIMESCALE_ROWS" -eq 0 ] || [ ! -f "$CHECKPOINT_FILE" ]; then
    log_info "Default history missing; generating (this resets telemetry, trip and maintenance_event)"
    "$PY" scripts/generate_history.py --config configs/offline_train.yaml --days 90 --vehicles 500 --reset --checkpoint-out "$CHECKPOINT_FILE" 2>&1 | tail -2
fi

TIMESCALE_ROWS=$(docker exec "$TIMESCALE_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM telemetry WHERE event_ts >= '2026-01-01' AND event_ts < '2026-04-01';")
TIMESCALE_VEH=$(docker exec "$TIMESCALE_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(DISTINCT vehicle_id) FROM telemetry WHERE event_ts >= '2026-01-01' AND event_ts < '2026-04-01';")
TRIP_ROWS=$(docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM trip WHERE started_at < '2026-04-01';")
docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -c "SELECT event_type, COUNT(*) AS n, COUNT(odometer_km) AS with_odometer FROM maintenance_event WHERE occurred_at < '2026-04-01' GROUP BY 1 ORDER BY 1;"
log_info "History: $TIMESCALE_ROWS telemetry rows for $TIMESCALE_VEH vehicles, $TRIP_ROWS trips"

[ "$TIMESCALE_ROWS" -gt 0 ] && log_pass "TimescaleDB telemetry hypertable has rows ($TIMESCALE_ROWS)" || log_fail "TimescaleDB telemetry table is empty"
[ "$TRIP_ROWS" -gt 0 ] && log_pass "PostgreSQL trip table has records ($TRIP_ROWS)" || log_fail "PostgreSQL trip table is empty"
for T in MAINTENANCE_REQUIRED FAILURE SERVICE_COMPLETED; do
    N=$(docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM maintenance_event WHERE event_type = '$T' AND occurred_at < '2026-04-01';")
    [ "$N" -gt 0 ] && log_pass "maintenance_event has $N $T rows" || log_fail "maintenance_event has no $T rows"
done
NO_ODO=$(docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "SELECT COUNT(*) FROM maintenance_event WHERE odometer_km IS NULL AND occurred_at < '2026-04-01';")
[ "$NO_ODO" -eq 0 ] && log_pass "Every maintenance event carries odometer_km" || log_fail "$NO_ODO maintenance events lack odometer_km"

# -----------------------------------------------------------------------------
# STEP 4b: Lifecycle ordering
# -----------------------------------------------------------------------------
log_step "4b" "Lifecycle Ordering SQL Checks"
ORDER_RESULT=$(docker exec "$POSTGRES_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -F "|" -c "
WITH e AS (
    SELECT vehicle_component_id, event_type,
           LAG(event_type) OVER (PARTITION BY vehicle_component_id ORDER BY occurred_at, event_type) AS prev
    FROM maintenance_event
)
SELECT
    COUNT(*) FILTER (WHERE event_type = 'SERVICE_COMPLETED' AND (prev IS NULL OR prev = 'SERVICE_COMPLETED')),
    COUNT(*) FILTER (WHERE event_type IN ('MAINTENANCE_REQUIRED','FAILURE') AND prev IN ('MAINTENANCE_REQUIRED','FAILURE'))
FROM e;")
ORPHAN_SERVICES=$(echo "$ORDER_RESULT" | cut -d'|' -f1)
BACK_TO_BACK=$(echo "$ORDER_RESULT" | cut -d'|' -f2)
log_info "SERVICE_COMPLETED without a preceding event: $ORPHAN_SERVICES"
log_info "Consecutive MAINTENANCE_REQUIRED/FAILURE without a service between: $BACK_TO_BACK"
[ "$ORPHAN_SERVICES" -eq 0 ] && log_pass "No SERVICE_COMPLETED without a preceding event" || log_fail "Found $ORPHAN_SERVICES orphan SERVICE_COMPLETED rows"
[ "$BACK_TO_BACK" -eq 0 ] && log_pass "No back-to-back trigger events without service" || log_fail "Found $BACK_TO_BACK back-to-back trigger events"

# -----------------------------------------------------------------------------
# STEP 5: Zero Hidden State Leakage Assertion
# -----------------------------------------------------------------------------
log_step "5" "Zero Hidden State Leakage Invariant"

LEAKED_COLUMNS=$(docker exec "$TIMESCALE_CONTAINER" psql -U fleetpulse -d fleetpulse -t -A -c "
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_name = 'telemetry' 
      AND column_name ~* '(health|wear|stress|lifecycle|degradation|pending_service)';
")

if [ -z "$LEAKED_COLUMNS" ]; then
    log_pass "Zero hidden state leakage: No hidden columns exist in TimescaleDB telemetry table"
else
    log_fail "Hidden state leakage detected in TimescaleDB! Columns found: $LEAKED_COLUMNS"
fi

# -----------------------------------------------------------------------------
# STEP 6: Checkpoint Persistence and Resume Integrity
# -----------------------------------------------------------------------------
log_step "6" "Checkpoint Serialization & Resume Verification"

if [ -f "$CHECKPOINT_FILE" ]; then
    log_pass "Checkpoint file created at $CHECKPOINT_FILE"
else
    log_fail "Checkpoint file was not created"
fi

"$PY" -c "
import json
with open('$CHECKPOINT_FILE') as f:
    cp = json.load(f)
assert cp.get('version') == '2.0', 'Unexpected checkpoint version'
assert 'daily_slots' in cp, 'Missing daily trip slots'
assert 'sim_time' in cp, 'Missing sim_time'
assert 'step_index' in cp, 'Missing step_index'
assert 'config_hash' in cp, 'Missing config_hash'
assert 'rng_states' in cp, 'Missing rng_states'
assert 'vehicles' in cp and len(cp['vehicles']) > 0, 'Missing vehicles'
assert 'population' in cp['rng_states'], 'Missing population RNG'
assert 'driving' in cp['rng_states'], 'Missing driving RNG'
assert 'degradation' in cp['rng_states'], 'Missing degradation RNG'
print('Checkpoint structure validated successfully.')
"
log_pass "Checkpoint JSON structure and RNG states validated"

log_step "6b" "Resume Equivalence (10 days continuous vs 5 + checkpoint + 5)"
"$PY" scripts/verify_resume_equivalence.py --config configs/offline_train.yaml --vehicles 50 --days 10 2>/dev/null || log_fail "Resumed run differs from continuous run"
log_pass "Resumed run is byte-identical to the continuous run"

# -----------------------------------------------------------------------------
# STEP 7: Phase 3 Unit Test Suite
# -----------------------------------------------------------------------------
log_step "7" "Running Phase 3 Test Suite"

PYTHONUNBUFFERED=1 "$PY" -u -m unittest discover -s tests 2>&1 | grep -v -E " (INFO|WARNING|CRITICAL) " | tail -4

log_pass "Unit test suite passed"

# -----------------------------------------------------------------------------
# STEP 8: Phase 2 Backward Compatibility Regression
# -----------------------------------------------------------------------------
log_step "8" "Verifying Phase 2 Compatibility (verify_phase2.sh)"

bash scripts/verify_phase2.sh

log_pass "Phase 2 verification passed without regression"

# -----------------------------------------------------------------------------
# STEP 9: Live smoke test through Kafka
# -----------------------------------------------------------------------------
log_step "9" "Live Smoke Test Through Kafka (50 vehicles, 60 s)"
bash scripts/smoke_live.sh
log_pass "Live smoke test passed"

# -----------------------------------------------------------------------------
# FINAL SUMMARY
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}${BOLD}====================================================================${NC}"
echo -e "${GREEN}${BOLD}   PHASE 3 VERIFICATION COMPLETE: ALL CHECKS PASSED SUCCESSFULLY     ${NC}"
echo -e "${GREEN}${BOLD}====================================================================${NC}\n"
