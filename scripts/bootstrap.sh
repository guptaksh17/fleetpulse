#!/usr/bin/env bash
# One-command demo setup: stack, schema, populations, users, history, features, dataset,
# models, risk scores. Re-runnable. Usage:
#   cp .env.example .env   # set secrets first
#   bash scripts/bootstrap.sh            # 500-vehicle, 90-day training cohort (about 25 min)
#   FLEET_100K=1 bash scripts/bootstrap.sh   # also seed 100K vehicles + 8 h of fleet history (about 1 h more)
set -euo pipefail
cd "$(dirname "$0")/.."
# Host-side tools talk to the project Redis on its mapped port (see docker-compose.yml).
export REDIS_HOST="${REDIS_HOST:-localhost}" REDIS_PORT="${REDIS_PORT:-6380}"
PY="${PYTHON:-.venv/bin/python}"; [ -x "$PY" ] || PY=python3
[ -f .env ] || { echo "Create .env from .env.example first"; exit 1; }
set -a; . ./.env; set +a

docker compose up -d --build
until docker exec postgres pg_isready -U fleetpulse -d fleetpulse >/dev/null 2>&1; do sleep 2; done
for m in db/postgres/migrations/*.sql; do docker exec -i postgres psql -U fleetpulse -d fleetpulse -q < "$m"; done
docker exec -i timescaledb psql -U fleetpulse -d fleetpulse -q < db/timescale/migrations/002_component_features.sql 2>/dev/null

"$PY" scripts/seed_population.py --config configs/offline_train.yaml
"$PY" scripts/seed_population.py --config configs/live_demo.yaml
"$PY" scripts/seed_users.py
if [ "$(docker exec postgres psql -U fleetpulse -d fleetpulse -t -A -c "SELECT count(*) FROM maintenance_event WHERE occurred_at < '2026-04-01'")" -eq 0 ]; then
  "$PY" scripts/generate_history.py --config configs/offline_train.yaml --days 90 --vehicles 500 --reset --checkpoint-out data/checkpoints/offline_checkpoint.json
fi
"$PY" scripts/build_features_offline.py --out data/features/f1
"$PY" scripts/build_dataset.py
"$PY" ml/train.py
"$PY" ml/score.py batch

if [ "${FLEET_100K:-0}" = "1" ]; then
  "$PY" scripts/seed_population.py --config configs/fleet_100k.yaml
  "$PY" scripts/generate_history.py --config configs/fleet_100k.yaml --days 0.37 --checkpoint-out data/checkpoints/fleet_100k_checkpoint.json
  "$PY" scripts/build_features_offline.py --sim-config configs/fleet_100k.yaml --start 2026-04-01T00:00:00Z --end 2026-04-02T00:00:00Z --out data/features/fleet100k --chunk 250
  "$PY" ml/score.py batch --features data/features/fleet100k
fi
echo "Ready: dashboard http://localhost:3000, API http://localhost:8000/docs (users in .env: admin@ / manager@ / viewer@fleetpulse.local)"
